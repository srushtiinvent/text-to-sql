"""Static SQL guard: parse the query into a tree and reject anything that is not a pure read.

This is a *helpful* layer (clear error messages, cheap early rejection), not the
safety boundary. The database-level protections in the executors are what
actually keep data safe. A string check like `sql.startswith("SELECT")` is
trivially bypassed, e.g. in Postgres:

    WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d

which is why this walks the whole tree, including CTEs and subqueries.
"""
from __future__ import annotations

from dataclasses import dataclass

_FORBIDDEN_NODE_NAMES = [
    "Insert", "Update", "Delete", "Merge", "Drop", "Create", "Alter", "AlterTable",
    "TruncateTable", "Command", "Into", "Grant", "Revoke", "Copy", "Set", "Pragma",
    "Transaction", "Commit", "Rollback", "Use", "Attach", "Detach", "LoadData", "Lock",
]
_ROOT_NODE_NAMES = ["Select", "Union", "Intersect", "Except", "SetOperation", "Subquery"]

# Functions with side effects or that read server files / hold the connection.
DENIED_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_read_file", "pg_read_binary_file",
    "pg_ls_dir", "pg_stat_file", "lo_import", "lo_export", "dblink", "dblink_exec",
    "pg_terminate_backend", "pg_cancel_backend", "set_config", "pg_advisory_lock",
    "pg_advisory_xact_lock", "pg_reload_conf", "pg_rotate_logfile", "nextval", "setval",
    "load_extension", "readfile", "writefile",
}


@dataclass
class GuardVerdict:
    ok: bool
    reason: str = ""


def check_sql(sql: str, dialect: str) -> GuardVerdict:
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return GuardVerdict(True, "sqlglot not installed; relying on database-level protection")

    forbidden = tuple(getattr(exp, n) for n in _FORBIDDEN_NODE_NAMES if hasattr(exp, n))
    roots = tuple(getattr(exp, n) for n in _ROOT_NODE_NAMES if hasattr(exp, n))
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except Exception as e:  # sqlglot.errors.ParseError and friends
        # Do not block on parser gaps: the database will give the real error.
        return GuardVerdict(True, f"unparsed ({type(e).__name__}); deferring to database")
    if len(statements) != 1:
        return GuardVerdict(False, f"expected exactly one statement, got {len(statements)}")
    stmt = statements[0]
    if not isinstance(stmt, roots):
        return GuardVerdict(False, f"only SELECT queries are allowed (got {type(stmt).__name__})")
    for node in stmt.walk():
        node = node[0] if isinstance(node, tuple) else node  # older sqlglot yields tuples
        if isinstance(node, forbidden):
            return GuardVerdict(False, f"forbidden operation in query: {type(node).__name__}")
        if isinstance(node, exp.Func):
            name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            if (name or "").lower() in DENIED_FUNCTIONS:
                return GuardVerdict(False, f"function not allowed: {name}")
    return GuardVerdict(True)
