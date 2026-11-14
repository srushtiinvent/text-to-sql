"""Database schema model, introspection (SQLite / Postgres) and prompt rendering."""
from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Column:
    name: str
    type: str = ""
    primary_key: bool = False
    description: str = ""
    value_description: str = ""
    samples: list[str] = field(default_factory=list)


@dataclass
class ForeignKey:
    table: str
    column: str
    ref_table: str
    ref_column: str


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    row_count: int | None = None

    def column(self, name: str) -> Column | None:
        low = name.lower()
        return next((c for c in self.columns if c.name.lower() == low), None)


@dataclass
class Schema:
    name: str
    dialect: str
    tables: dict[str, Table] = field(default_factory=dict)
    foreign_keys: list[ForeignKey] = field(default_factory=list)

    @property
    def column_count(self) -> int:
        return sum(len(t.columns) for t in self.tables.values())

    def table(self, name: str) -> Table | None:
        low = name.lower()
        return next((t for n, t in self.tables.items() if n.lower() == low), None)

    def key_columns(self, table: str) -> set[str]:
        t = self.tables[table]
        keys = {c.name for c in t.columns if c.primary_key}
        for fk in self.foreign_keys:
            if fk.table == table:
                keys.add(fk.column)
            if fk.ref_table == table:
                keys.add(fk.ref_column)
        return keys

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dialect": self.dialect,
            "tables": [
                {"name": t.name, "row_count": t.row_count,
                 "columns": [{"name": c.name, "type": c.type, "pk": c.primary_key,
                              "description": c.description} for c in t.columns]}
                for t in self.tables.values()
            ],
            "foreign_keys": [fk.__dict__ for fk in self.foreign_keys],
        }


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# ---------------------------------------------------------------- introspection

def introspect_sqlite(path: str | Path, name: str | None = None, sample_values: int = 3) -> Schema:
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        schema = Schema(name=name or Path(path).stem, dialect="sqlite")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for tname in tables:
            t = Table(name=tname)
            for _cid, cname, ctype, _notnull, _default, pk in conn.execute(
                    f"PRAGMA table_info({quote_ident(tname)})"):
                t.columns.append(Column(name=cname, type=(ctype or "").upper(), primary_key=bool(pk)))
            for row in conn.execute(f"PRAGMA foreign_key_list({quote_ident(tname)})"):
                # (id, seq, table, from, to, on_update, on_delete, match)
                ref_table, from_col, to_col = row[2], row[3], row[4]
                if to_col is None:  # FK to the referenced table's primary key
                    pk_cols = [r[1] for r in conn.execute(
                        f"PRAGMA table_info({quote_ident(ref_table)})") if r[5]]
                    to_col = pk_cols[0] if pk_cols else from_col
                schema.foreign_keys.append(ForeignKey(tname, from_col, ref_table, to_col))
            t.row_count = conn.execute(f"SELECT COUNT(*) FROM {quote_ident(tname)}").fetchone()[0]
            if sample_values:
                for c in t.columns:
                    try:
                        rows = conn.execute(
                            f"SELECT DISTINCT {quote_ident(c.name)} FROM {quote_ident(tname)} "
                            f"WHERE {quote_ident(c.name)} IS NOT NULL LIMIT {int(sample_values)}").fetchall()
                        c.samples = [_short(r[0]) for r in rows]
                    except sqlite3.Error:
                        pass
            schema.tables[tname] = t
        _fix_fk_case(schema)
        return schema
    finally:
        conn.close()


def introspect_postgres(dsn: str, name: str, sample_values: int = 3) -> Schema:
    import psycopg  # optional dependency

    schema = Schema(name=name, dialect="postgres")
    with psycopg.connect(dsn, autocommit=True) as conn:
        cols = conn.execute(
            """SELECT table_name, column_name, data_type FROM information_schema.columns
               WHERE table_schema = 'public' ORDER BY table_name, ordinal_position""").fetchall()
        for tname, cname, ctype in cols:
            schema.tables.setdefault(tname, Table(name=tname)).columns.append(
                Column(name=cname, type=ctype.upper()))
        constraint_sql = """
            SELECT c.contype, cl.relname, a.attname, rcl.relname, af.attname
            FROM pg_constraint c
            JOIN pg_class cl ON cl.oid = c.conrelid
            JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, n) ON true
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
            LEFT JOIN pg_class rcl ON rcl.oid = c.confrelid
            LEFT JOIN LATERAL unnest(c.confkey) WITH ORDINALITY AS fk(attnum, n) ON fk.n = k.n
            LEFT JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = fk.attnum
            WHERE c.contype IN ('p', 'f') AND c.connamespace = 'public'::regnamespace"""
        for contype, tname, col, ref_t, ref_c in conn.execute(constraint_sql).fetchall():
            if tname not in schema.tables:
                continue
            if contype == "p":
                c = schema.tables[tname].column(col)
                if c:
                    c.primary_key = True
            elif ref_t:
                schema.foreign_keys.append(ForeignKey(tname, col, ref_t, ref_c))
        for t in schema.tables.values():
            t.row_count = conn.execute(f"SELECT COUNT(*) FROM {quote_ident(t.name)}").fetchone()[0]
            if sample_values:
                for c in t.columns:
                    rows = conn.execute(
                        f"SELECT DISTINCT {quote_ident(c.name)} FROM {quote_ident(t.name)} "
                        f"WHERE {quote_ident(c.name)} IS NOT NULL LIMIT {int(sample_values)}").fetchall()
                    c.samples = [_short(r[0]) for r in rows]
    return schema


def _fix_fk_case(schema: Schema) -> None:
    """SQLite FK declarations are case-insensitive; normalise names to the real table/column names."""
    fixed = []
    for fk in schema.foreign_keys:
        t, rt = schema.table(fk.table), schema.table(fk.ref_table)
        if not t or not rt:
            continue
        c, rc = t.column(fk.column), rt.column(fk.ref_column)
        if c and rc:
            fixed.append(ForeignKey(t.name, c.name, rt.name, rc.name))
    schema.foreign_keys = fixed


def _short(v: object, n: int = 40) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


# ------------------------------------------------------ BIRD-style descriptions

def load_descriptions(schema: Schema, description_dir: str | Path) -> None:
    """Attach BIRD `database_description/<table>.csv` column descriptions to the schema.

    BIRD's CSVs are not consistently encoded, so try a few encodings.
    """
    d = Path(description_dir)
    if not d.is_dir():
        return
    for csv_path in d.glob("*.csv"):
        table = schema.table(csv_path.stem)
        if not table:
            continue
        text = None
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                text = csv_path.read_text(encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            continue
        for row in csv.DictReader(text.splitlines()):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            col = table.column(row.get("original_column_name", ""))
            if not col:
                continue
            desc = row.get("column_description") or row.get("column_name") or ""
            col.description = desc
            col.value_description = row.get("value_description", "")


# ------------------------------------------------------------------- rendering

def render_schema(schema: Schema, tables: list[str] | None = None,
                  columns: dict[str, list[str]] | None = None,
                  with_samples: bool = True) -> str:
    """Render a CREATE TABLE-like view of (part of) the schema for an LLM prompt."""
    names = tables if tables is not None else list(schema.tables)
    out = []
    for name in names:
        t = schema.tables[name]
        keep = set(columns[name]) if columns and name in columns else None
        lines = []
        for c in t.columns:
            if keep is not None and c.name not in keep:
                continue
            parts = [f"  {quote_ident(c.name)} {c.type}".rstrip()]
            if c.primary_key:
                parts.append("PRIMARY KEY")
            notes = []
            if c.description and c.description.lower() != c.name.lower():
                notes.append(c.description)
            if c.value_description:
                notes.append("values: " + re.sub(r"\s+", " ", c.value_description)[:200])
            if with_samples and c.samples:
                notes.append("e.g. " + ", ".join(repr(s) for s in c.samples))
            line = " ".join(parts)
            if notes:
                line += "  -- " + "; ".join(notes)
            lines.append(line)
        fks = [fk for fk in schema.foreign_keys if fk.table == name and fk.ref_table in names]
        for fk in fks:
            lines.append(f"  FOREIGN KEY ({quote_ident(fk.column)}) REFERENCES "
                         f"{quote_ident(fk.ref_table)}({quote_ident(fk.ref_column)})")
        rc = f"  -- {t.row_count} rows" if t.row_count is not None else ""
        out.append(f"CREATE TABLE {quote_ident(name)} ({rc}\n" + ",\n".join(lines) + "\n);")
    return "\n\n".join(out)
