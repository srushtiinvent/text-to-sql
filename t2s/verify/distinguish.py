"""Find a tiny database on which two disagreeing SQL queries give different answers.

Why: an LLM judge reviewing two 40-line queries is unreliable. The same judge
looking at six rows and two different outputs ("A counts the cancelled order,
B does not") is much better. This module turns a hard comparison into an easy one.

Algorithm
  1. Sample k rows from every table the queries touch (k = 8, 32, 128, ...),
     then add the parent rows their foreign keys point to, so joins are not empty.
  2. Load the sample into an in-memory SQLite database and run both queries.
     Stop growing k as soon as their outputs differ.
  3. Shrink the sample with delta debugging (ddmin): repeatedly try removing
     chunks of rows, keeping each removal that preserves the difference.
     The result is typically 1–8 rows.

Every test runs under the same read-only authorizer and timeout as real execution.
"""
from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, field

from ..execute.base import ExecutionError, jsonable
from ..execute.sqlite_exec import run_readonly
from ..schema import Schema, quote_ident
from .cluster import result_key


@dataclass
class DistinguishingExample:
    tables: dict[str, dict] = field(default_factory=dict)   # name -> {columns, rows}
    outputs: dict[str, dict] = field(default_factory=dict)  # label -> {columns, rows}
    sample_size: int = 0
    tests_run: int = 0
    rows_total: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    def render(self, max_rows: int = 25) -> str:
        parts = ["Example database (all other tables are empty):"]
        for name, t in self.tables.items():
            parts.append(f"\nTable {name}:\n" + _text_table(t["columns"], t["rows"], max_rows))
        for label, out in self.outputs.items():
            parts.append(f"\nOutput of query {label}:\n" + _text_table(out["columns"], out["rows"], max_rows))
        return "\n".join(parts)


# ----------------------------------------------------------------- row sources

class SQLiteRowSource:
    def __init__(self, executor):
        self.executor = executor

    def ddl(self, table: str, schema: Schema) -> str:
        with self.executor.connect_raw() as conn:
            row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                               (table,)).fetchone()
        return row[0] if row and row[0] else _generic_ddl(schema, table)

    def sample(self, table: str, k: int, seed: int) -> tuple[list[str], list[tuple]]:
        q = quote_ident(table)
        with self.executor.connect_raw() as conn:
            conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
            try:  # deterministic pseudo-random order by hashing rowid
                cur = conn.execute(
                    f"SELECT * FROM {q} ORDER BY (rowid * 2654435761 + ?) % 4294967296 LIMIT ?",
                    (seed, k))
            except sqlite3.OperationalError:  # WITHOUT ROWID tables
                cur = conn.execute(f"SELECT * FROM {q} LIMIT ?", (k,))
            return [d[0] for d in cur.description], cur.fetchall()

    def rows_matching(self, table: str, column: str, values: list) -> list[tuple]:
        if not values:
            return []
        out = []
        with self.executor.connect_raw() as conn:
            conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
            for i in range(0, len(values), 500):
                chunk = values[i:i + 500]
                ph = ",".join("?" * len(chunk))
                out += conn.execute(f"SELECT * FROM {quote_ident(table)} "
                                    f"WHERE {quote_ident(column)} IN ({ph})", chunk).fetchall()
        return out

    def to_sqlite(self, sql: str) -> str:
        return sql


class PostgresRowSource:
    """Copies sampled Postgres rows into SQLite; queries are transpiled with sqlglot."""

    def __init__(self, executor):
        self.executor = executor

    def ddl(self, table: str, schema: Schema) -> str:
        return _generic_ddl(schema, table)

    def sample(self, table, k, seed):
        cols, rows = self.executor.fetch(
            f"SELECT * FROM {quote_ident(table)} ORDER BY md5(ctid::text || %s) LIMIT %s",
            (str(seed), k))
        return cols, [tuple(jsonable(v) for v in r) for r in rows]

    def rows_matching(self, table, column, values):
        if not values:
            return []
        _, rows = self.executor.fetch(
            f"SELECT * FROM {quote_ident(table)} WHERE {quote_ident(column)} = ANY(%s)",
            (list(values),))
        return [tuple(jsonable(v) for v in r) for r in rows]

    def to_sqlite(self, sql: str) -> str:
        import sqlglot
        return sqlglot.transpile(sql, read="postgres", write="sqlite")[0]


def _generic_ddl(schema: Schema, table: str) -> str:
    cols = []
    for c in schema.tables[table].columns:
        t = c.type.upper()
        aff = ("INTEGER" if "INT" in t else "REAL" if any(x in t for x in ("NUM", "DEC", "REAL", "DOUBLE", "FLOAT"))
               else "TEXT")
        cols.append(f"{quote_ident(c.name)} {aff}")
    return f"CREATE TABLE {quote_ident(table)} ({', '.join(cols)})"


# -------------------------------------------------------------------- search

def referenced_tables(sql: str, schema: Schema) -> list[str]:
    """Tables whose name appears as an identifier in the SQL (works without a parser)."""
    found = []
    for name in schema.tables:
        pat = rf'(?<![\w"`\[]){re.escape(name)}(?![\w"`\]])|"{re.escape(name)}"|`{re.escape(name)}`|\[{re.escape(name)}\]'
        if re.search(pat, sql, re.I):
            found.append(name)
    return found


def find_distinguishing_example(source, schema: Schema, sqls: dict[str, str], *,
                                sample_sizes=(8, 32, 128, 512, 2048), budget: int = 250,
                                seed: int = 7, timeout_s: float = 5.0) -> DistinguishingExample | None:
    try:
        lite = {label: source.to_sqlite(sql) for label, sql in sqls.items()}
    except Exception:
        return None  # could not transpile (e.g. sqlglot missing for Postgres)
    tables = sorted({t for s in sqls.values() for t in referenced_tables(s, schema)})
    if not tables:
        return None
    ddl = {t: source.ddl(t, schema) for t in tables}
    tester = _Tester(ddl, lite, timeout_s)

    for k in sample_sizes:
        rows = _sample_with_fk_closure(source, schema, tables, k, seed)
        items = [(t, r) for t in tables for r in rows[t][1]]
        if not items:
            continue
        if not tester.differs(items):
            if all((schema.tables[t].row_count or 0) <= k for t in tables):
                return None  # whole tables already loaded and the queries still agree
            continue
        columns = {t: rows[t][0] for t in tables}
        minimal = _ddmin(items, tester.differs, budget)
        outputs = tester.outputs(minimal)
        by_table: dict[str, list] = {}
        for t, r in minimal:
            by_table.setdefault(t, []).append([jsonable(v) for v in r])
        return DistinguishingExample(
            tables={t: {"columns": columns[t], "rows": by_table[t]} for t in by_table},
            outputs={label: {"columns": out.columns,
                             "rows": [[jsonable(v) for v in r] for r in out.rows]}
                     for label, out in outputs.items()},
            sample_size=k, tests_run=tester.tests, rows_total=len(minimal))
    return None


def _sample_with_fk_closure(source, schema, tables, k, seed):
    rows = {}
    for t in tables:
        cols, rs = source.sample(t, k, seed)
        rows[t] = (cols, list(dict.fromkeys(rs)))
    fks = [fk for fk in schema.foreign_keys if fk.table in rows and fk.ref_table in rows]
    for _ in range(2):  # two passes reach grandparents
        for fk in fks:
            ccols, crows = rows[fk.table]
            pcols, prows = rows[fk.ref_table]
            ci, pi = ccols.index(fk.column), pcols.index(fk.ref_column)
            have = {r[pi] for r in prows}
            need = sorted({r[ci] for r in crows if r[ci] is not None and r[ci] not in have}, key=str)
            if need:
                prows.extend(source.rows_matching(fk.ref_table, fk.ref_column, need))
                rows[fk.ref_table] = (pcols, list(dict.fromkeys(prows)))
    return rows


class _Tester:
    def __init__(self, ddl: dict[str, str], sqls: dict[str, str], timeout_s: float):
        self.ddl, self.sqls, self.timeout_s = ddl, sqls, timeout_s
        self.tests = 0

    def _db(self, items) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        for t, stmt in self.ddl.items():
            conn.execute(stmt)
        by_table: dict[str, list] = {}
        for t, r in items:
            by_table.setdefault(t, []).append(r)
        for t, rs in by_table.items():
            ph = ",".join("?" * len(rs[0]))
            conn.executemany(f"INSERT INTO {quote_ident(t)} VALUES ({ph})", rs)
        return conn

    def outputs(self, items) -> dict:
        conn = self._db(items)
        try:
            return {label: run_readonly(conn, sql, self.timeout_s, 10_000)
                    for label, sql in self.sqls.items()}
        finally:
            conn.close()

    def differs(self, items) -> bool:
        self.tests += 1
        try:
            outs = self.outputs(items)
        except (ExecutionError, sqlite3.Error):
            return False
        return len({result_key(o) for o in outs.values()}) > 1


def _ddmin(items: list, differs, budget: int) -> list:
    """Zeller's delta debugging (complement-only variant), bounded by `budget` tests."""
    n, tests = 2, 0
    while len(items) >= 2 and tests < budget:
        chunk = math.ceil(len(items) / n)
        reduced = False
        for i in range(0, len(items), chunk):
            if tests >= budget:
                break
            complement = items[:i] + items[i + chunk:]
            tests += 1
            if complement and differs(complement):
                items, n, reduced = complement, max(n - 1, 2), True
                break
        if not reduced:
            if n >= len(items):
                break
            n = min(len(items), n * 2)
    return items


def _text_table(columns: list[str], rows: list, max_rows: int) -> str:
    rows = [["NULL" if v is None else str(v) for v in r] for r in rows[:max_rows]]
    widths = [max([len(str(c))] + [len(r[i]) for r in rows]) for i, c in enumerate(columns)]
    fmt = lambda vals: "| " + " | ".join(v.ljust(w) for v, w in zip(vals, widths)) + " |"
    out = [fmt([str(c) for c in columns]), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    out += [fmt(r) for r in rows] or ["(no rows)"]
    return "\n".join(out)

