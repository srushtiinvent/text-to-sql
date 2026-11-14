"""Read-only SQLite execution with three independent protections.

1. The file is opened with `mode=ro`, so the OS-level handle cannot write.
2. An authorizer callback allows only read actions. It runs when the statement
   is *prepared*, so a write hidden inside a CTE or trigger is rejected before
   anything executes.
3. A progress handler aborts queries that run past the deadline, and fetching
   stops at `row_limit`.

The static SQL guard (guard.py) is a fourth, optional layer. It gives nicer
error messages but is never the thing that keeps data safe.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .base import ExecutionError, ResultSet
from .guard import check_sql

_ALLOWED_ACTIONS = {
    getattr(sqlite3, name) for name in
    ("SQLITE_SELECT", "SQLITE_READ", "SQLITE_FUNCTION", "SQLITE_RECURSIVE")
    if hasattr(sqlite3, name)
}
# Functions that read nothing but could still be abused (e.g. load_extension if enabled).
_DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


def _authorizer(action, arg1, arg2, dbname, source):
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == getattr(sqlite3, "SQLITE_FUNCTION", -1) and (arg2 or "").lower() in _DENIED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def run_readonly(conn: sqlite3.Connection, sql: str, timeout_s: float, row_limit: int) -> ResultSet:
    """Run one statement on an existing connection with the authorizer + deadline installed."""
    deadline = time.monotonic() + timeout_s
    timed_out = False

    def progress():
        nonlocal timed_out
        if time.monotonic() > deadline:
            timed_out = True
            return 1  # non-zero aborts the statement
        return 0

    conn.set_authorizer(_authorizer)
    conn.set_progress_handler(progress, 2000)
    start = time.perf_counter()
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchmany(row_limit + 1)
        truncated = len(rows) > row_limit
        return ResultSet(cols, [tuple(r) for r in rows[:row_limit]], truncated,
                         (time.perf_counter() - start) * 1000)
    except sqlite3.DatabaseError as e:
        if timed_out:
            raise ExecutionError(f"query exceeded {timeout_s}s timeout", kind="timeout") from e
        msg = str(e)
        if "not authorized" in msg or "prohibited" in msg:
            raise ExecutionError("query attempted a non-read operation", kind="guard") from e
        if "one statement at a time" in msg:
            raise ExecutionError("only one statement is allowed", kind="guard") from e
        raise ExecutionError(msg, kind="db") from e
    except sqlite3.Warning as e:  # e.g. "You can only execute one statement at a time."
        raise ExecutionError(str(e), kind="guard") from e
    finally:
        conn.set_progress_handler(None, 0)
        conn.set_authorizer(None)


class SQLiteExecutor:
    dialect = "sqlite"

    def __init__(self, path: str | Path, timeout_s: float = 10.0, row_limit: int = 10000,
                 static_guard: bool = True):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self.timeout_s = timeout_s
        self.row_limit = row_limit
        self.static_guard = static_guard
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        # sqlite3 connections are not thread-safe; keep one per worker thread.
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True,
                                   check_same_thread=False)
            conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
            self._local.conn = conn
        return conn

    def run(self, sql: str) -> ResultSet:
        if self.static_guard:
            verdict = check_sql(sql, "sqlite")
            if not verdict.ok:
                raise ExecutionError(verdict.reason, kind="guard")
        return run_readonly(self._conn(), sql, self.timeout_s, self.row_limit)

    def connect_raw(self) -> sqlite3.Connection:
        """A fresh read-only connection (used by the distinguisher to copy sample rows)."""
        return sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
