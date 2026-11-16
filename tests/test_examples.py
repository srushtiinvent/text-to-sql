"""The recorded examples shipped with the frontend must have the shape the UI reads."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_recorded_examples_have_the_fields_the_frontend_reads():
    examples = json.loads((ROOT / "web/public/examples.json").read_text())
    assert examples
    for t in examples:
        assert t["question"] and t["retrieval"]["tables"]
        assert "schema_text" not in t["retrieval"]  # stripped to keep the file small
        labels = {c["label"] for c in t["candidates"]}
        for k in t["clusters"]:
            assert {"id", "members", "size", "representative"} <= set(k)
        assert 0 <= t["confidence"]["score"] <= 1
        if t["distinguishing"]:
            assert set(t["distinguishing"]["outputs"]) <= labels
        if t["answer"]:
            assert {"sql", "columns", "rows", "row_count", "candidate"} <= set(t["answer"])
