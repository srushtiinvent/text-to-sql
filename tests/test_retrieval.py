import asyncio

from t2s.execute.sqlite_exec import SQLiteExecutor
from t2s.retrieval import Retriever
from t2s.retrieval.bm25 import BM25
from t2s.retrieval.joins import connect_tables
from t2s.retrieval.text import split_identifier, tokenize
from t2s.retrieval.values import ValueIndex, candidate_spans
from t2s.schema import introspect_sqlite, quote_ident


def _values(shop_db, schema):
    ex = SQLiteExecutor(shop_db)

    def fetch(t, c, limit):
        return [r[0] for r in ex.run(f"SELECT DISTINCT {quote_ident(c)} FROM {quote_ident(t)} LIMIT {limit}").rows]

    return ValueIndex.build(fetch, schema)


def test_tokenize_and_split():
    assert split_identifier("custID") == "cust id"
    assert split_identifier("order_items") == "order items"
    assert tokenize("Which customers spent the most?") == ["customer", "spent", "most"]


def test_bm25_ranks_relevant_doc_first():
    docs = [tokenize("payments method amount paid"), tokenize("customers city segment"),
            tokenize("products category price")]
    scores = BM25(docs).scores(tokenize("which payment method"))
    assert scores.index(max(scores)) == 0


def test_value_index_matches_fuzzy_literals(shop_db):
    schema = introspect_sqlite(shop_db)
    idx = _values(shop_db, schema)
    m = idx.match("customers living in pune who bought electronic items")
    found = {(x.table, x.column, x.value) for x in m}
    assert ("customers", "city", "Pune") in found
    assert ("products", "category", "Electronics") in found


def test_candidate_spans_include_quoted():
    assert "Wireless Earbuds" in candidate_spans('how many "Wireless Earbuds" sold')


def test_join_path_adds_bridge_tables(shop_db):
    schema = introspect_sqlite(shop_db)
    tables, bridges = connect_tables(schema, ["customers", "products"])
    assert set(bridges) == {"orders", "order_items"}
    assert set(tables) == {"customers", "products", "orders", "order_items"}


def test_retriever_prunes_large_schema(shop_db, cfg):
    schema = introspect_sqlite(shop_db)
    cfg = cfg.with_overrides(full_schema_column_threshold=5, top_k_tables=2)
    r = Retriever(schema, _values(shop_db, schema), cfg)
    res = asyncio.run(r.retrieve("Which payment method do customers in Pune use most?"))
    assert res.mode == "retrieved"
    assert {"payments", "customers"} <= set(res.tables)
    assert "orders" in res.tables  # bridge between payments and customers
    assert "Pune" in res.hints_text()


def test_small_schema_uses_full_prompt(shop_db, cfg):
    schema = introspect_sqlite(shop_db)
    res = asyncio.run(Retriever(schema, None, cfg).retrieve("how many orders?"))
    assert res.mode == "full" and len(res.tables) == 5
