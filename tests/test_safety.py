"""The executor must never modify data, whatever SQL the model writes."""
import pytest

from t2s.execute.base import ExecutionError
from t2s.execute.sqlite_exec import SQLiteExecutor

WRITES = [
    "DELETE FROM orders",
    "UPDATE customers SET city = 'X'",
    "INSERT INTO products VALUES (999, 'x', 'y', 1)",
    "DROP TABLE orders",
    "CREATE TABLE evil (x INT)",
    "ATTACH DATABASE '/tmp/evil.db' AS evil",
    "PRAGMA writable_schema = 1",
    "SELECT 1; DELETE FROM orders",
    # a write hidden inside a CTE: passes a naive startswith('WITH'/'SELECT') check
    "WITH d AS (SELECT 1) DELETE FROM orders",
    "CREATE TEMP TABLE t AS SELECT * FROM orders",
]


@pytest.mark.parametrize("sql", WRITES)
@pytest.mark.parametrize("static_guard", [True, False])
def test_writes_are_rejected(shop_db, sql, static_guard):
    ex = SQLiteExecutor(shop_db, static_guard=static_guard)
    before = ex.run("SELECT COUNT(*) FROM orders").rows[0][0]
    with pytest.raises(ExecutionError):
        ex.run(sql)
    assert ex.run("SELECT COUNT(*) FROM orders").rows[0][0] == before


def test_database_level_protection_works_without_static_guard(shop_db):
    """The authorizer, not the parser, is the safety boundary."""
    ex = SQLiteExecutor(shop_db, static_guard=False)
    with pytest.raises(ExecutionError) as e:
        ex.run("DELETE FROM orders")
    assert e.value.kind in ("guard", "db")


def test_timeout(shop_db):
    ex = SQLiteExecutor(shop_db, timeout_s=0.2)
    slow = ("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) "
            "SELECT COUNT(*) FROM n")
    with pytest.raises(ExecutionError) as e:
        ex.run(slow)
    assert e.value.kind == "timeout"


def test_row_limit(shop_db):
    ex = SQLiteExecutor(shop_db, row_limit=10)
    r = ex.run("SELECT * FROM orders")
    assert len(r.rows) == 10 and r.truncated


def test_reads_work(shop_db):
    ex = SQLiteExecutor(shop_db)
    r = ex.run("WITH t AS (SELECT city, COUNT(*) n FROM customers GROUP BY city) SELECT * FROM t")
    assert r.columns == ["city", "n"] and len(r.rows) == 8


def test_static_guard_tree_walk():
    pytest.importorskip("sqlglot")
    from t2s.execute.guard import check_sql

    assert check_sql("SELECT * FROM t", "postgres").ok
    assert not check_sql("WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d", "postgres").ok
    assert not check_sql("SELECT * INTO backup FROM users", "postgres").ok
    assert not check_sql("SELECT pg_sleep(100)", "postgres").ok
    assert not check_sql("SELECT 1; SELECT 2", "postgres").ok
