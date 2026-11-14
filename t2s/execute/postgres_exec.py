"""Read-only Postgres execution.

Protections, strongest first:
1. Connect as a role that only has SELECT grants (see docker/postgres/init.sql).
   This is the real guarantee; everything below is defence in depth.
2. Every query runs inside `BEGIN TRANSACTION READ ONLY`, which also blocks
   `WITH d AS (DELETE ...) SELECT ...` and `SELECT ... INTO new_table`.
3. `SET LOCAL statement_timeout` kills slow queries (including pg_sleep).
4. The transaction is always rolled back.

A new connection per query keeps the code simple and thread-safe. Under real
load you would use psycopg_pool; that trade-off is noted in docs/design.md.
"""
from __future__ import annotations

import time

from .base import ExecutionError, ResultSet
from .guard import check_sql


class PostgresExecutor:
    dialect = "postgres"

    def __init__(self, dsn: str, timeout_s: float = 10.0, row_limit: int = 10000,
                 static_guard: bool = True):
        self.dsn = dsn
        self.timeout_s = timeout_s
        self.row_limit = row_limit
        self.static_guard = static_guard

    def run(self, sql: str) -> ResultSet:
        import psycopg

        if self.static_guard:
            verdict = check_sql(sql, "postgres")
            if not verdict.ok:
                raise ExecutionError(verdict.reason, kind="guard")
        start = time.perf_counter()
        try:
            with psycopg.connect(self.dsn, autocommit=True) as conn:
                conn.execute("BEGIN TRANSACTION READ ONLY")
                try:
                    conn.execute(f"SET LOCAL statement_timeout = {int(self.timeout_s * 1000)}")
                    cur = conn.execute(sql)
                    cols = [d.name for d in (cur.description or [])]
                    rows = cur.fetchmany(self.row_limit + 1) if cur.description else []
                finally:
                    conn.execute("ROLLBACK")
        except psycopg.errors.QueryCanceled as e:
            raise ExecutionError(f"query exceeded {self.timeout_s}s timeout", kind="timeout") from e
        except (psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege) as e:
            raise ExecutionError(f"query attempted a non-read operation: {e}", kind="guard") from e
        except psycopg.Error as e:
            raise ExecutionError(str(e).strip(), kind="db") from e
        truncated = len(rows) > self.row_limit
        return ResultSet(cols, [tuple(r) for r in rows[: self.row_limit]], truncated,
                         (time.perf_counter() - start) * 1000)

    # Used by the distinguisher to copy sample rows into an in-memory SQLite DB.
    def fetch(self, sql: str, params: tuple = ()) -> tuple[list[str], list[tuple]]:
        import psycopg

        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute("BEGIN TRANSACTION READ ONLY")
            try:
                cur = conn.execute(sql, params)
                return [d.name for d in cur.description], [tuple(r) for r in cur.fetchall()]
            finally:
                conn.execute("ROLLBACK")
