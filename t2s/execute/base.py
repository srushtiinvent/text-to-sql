"""Shared types for query execution."""
from __future__ import annotations

import datetime as dt
import decimal
import os
from dataclasses import dataclass, field
from typing import Any, Protocol


class ExecutionError(Exception):
    """A query was rejected or failed. `kind` is one of: guard, timeout, db."""

    def __init__(self, message: str, kind: str = "db"):
        super().__init__(message)
        self.kind = kind


@dataclass
class ResultSet:
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False
    elapsed_ms: float = 0.0

    def preview(self, n: int) -> dict:
        return {"columns": self.columns, "rows": [list(map(jsonable, r)) for r in self.rows[:n]],
                "row_count": len(self.rows), "truncated": self.truncated,
                "elapsed_ms": round(self.elapsed_ms, 2)}


def jsonable(v: Any) -> Any:
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    return v


class Executor(Protocol):
    dialect: str

    def run(self, sql: str) -> ResultSet: ...


def make_executor(spec, cfg) -> "Executor":
    """Build an executor for a configured database."""
    if spec.dialect == "sqlite":
        from .sqlite_exec import SQLiteExecutor
        return SQLiteExecutor(cfg.resolve(spec.path), timeout_s=cfg.timeout_s, row_limit=cfg.row_limit)
    if spec.dialect == "postgres":
        from .postgres_exec import PostgresExecutor
        dsn = os.environ.get(spec.dsn_env or "", "")
        if not dsn:
            raise RuntimeError(f"database {spec.name}: env var {spec.dsn_env} is not set")
        return PostgresExecutor(dsn, timeout_s=cfg.timeout_s, row_limit=cfg.row_limit)
    raise ValueError(f"unsupported dialect: {spec.dialect}")
