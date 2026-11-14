from t2s.execute.base import ResultSet
from t2s.execute.sqlite_exec import SQLiteExecutor
from t2s.schema import introspect_sqlite
from t2s.verify.cluster import cluster_candidates, result_key, set_equal
from t2s.verify.distinguish import (SQLiteRowSource, find_distinguishing_example,
                                    referenced_tables)


class C:  # minimal stand-in for generate.Candidate
    def __init__(self, id, sql, rows):
        self.id, self.sql, self.label = id, sql, chr(65 + id)
        self.result, self.error, self.cluster = ResultSet(["x"], rows), None, None

    @property
    def ok(self):
        return True


def test_cluster_ignores_row_and_column_order_and_number_types():
    a = ResultSet(["n", "c"], [("Pune", 3), ("Delhi", 2.0)])
    b = ResultSet(["c", "n"], [(2, "Delhi"), (3.0, "Pune")])
    assert result_key(a) == result_key(b)


def test_cluster_keeps_duplicates_distinct():
    assert result_key(ResultSet(["x"], [(1,), (1,)])) != result_key(ResultSet(["x"], [(1,)]))


def test_bird_set_equal_ignores_duplicates():
    assert set_equal(ResultSet(["x"], [(1,), (1,)]), ResultSet(["x"], [(1,)]))


def test_clusters_sorted_by_size_and_simplest_representative():
    cands = [C(0, "SELECT a FROM t WHERE x = 1 AND 1 = 1", [(1,)]),
             C(1, "SELECT a FROM t", [(2,)]),
             C(2, "SELECT a FROM t WHERE x = 1", [(1,)])]
    cl = cluster_candidates(cands)
    assert [c.size for c in cl] == [2, 1]
    assert cl[0].representative == 2  # fewer tokens than candidate 0


def test_referenced_tables(shop_db):
    schema = introspect_sqlite(shop_db)
    sql = 'SELECT * FROM "orders" o JOIN customers c ON c.customer_id = o.customer_id'
    assert referenced_tables(sql, schema) == ["customers", "orders"]


def test_distinguishing_example_is_tiny_and_explains_difference(shop_db):
    schema = introspect_sqlite(shop_db)
    src = SQLiteRowSource(SQLiteExecutor(shop_db))
    good = ("SELECT c.name FROM customers c JOIN orders o ON o.customer_id = c.customer_id "
            "WHERE o.status = 'completed' GROUP BY c.customer_id HAVING SUM(o.total_amount) > 10000")
    bad = ("SELECT c.name FROM customers c JOIN orders o ON o.customer_id = c.customer_id "
           "GROUP BY c.customer_id HAVING SUM(o.total_amount) > 10000")
    ex = find_distinguishing_example(src, schema, {"A": good, "B": bad})
    assert ex is not None
    assert ex.rows_total <= 4
    statuses = {r[3] for r in ex.tables["orders"]["rows"]}
    assert statuses & {"cancelled", "refunded"}  # the difference is a non-completed order
    assert ex.outputs["A"]["rows"] != ex.outputs["B"]["rows"]
    assert "Output of query A" in ex.render()


def test_distinct_difference_is_found(shop_db):
    schema = introspect_sqlite(shop_db)
    src = SQLiteRowSource(SQLiteExecutor(shop_db))
    a = "SELECT COUNT(DISTINCT customer_id) FROM orders"
    b = "SELECT COUNT(customer_id) FROM orders"
    ex = find_distinguishing_example(src, schema, {"A": a, "B": b})
    assert ex is not None and ex.rows_total == 2  # two orders by the same customer


def test_equivalent_queries_have_no_example(shop_db):
    schema = introspect_sqlite(shop_db)
    src = SQLiteRowSource(SQLiteExecutor(shop_db))
    a = "SELECT COUNT(*) FROM customers WHERE city = 'Pune'"
    b = "SELECT SUM(CASE WHEN city = 'Pune' THEN 1 ELSE 0 END) FROM customers"
    assert find_distinguishing_example(src, schema, {"A": a, "B": b},
                                       sample_sizes=(8, 32, 256)) is None
