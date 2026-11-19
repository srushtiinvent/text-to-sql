"""End-to-end pipeline and eval harness with a scripted fake LLM (no network)."""
import asyncio
import json
import re
from pathlib import Path

from t2s.config import CandidateSpec
from t2s.llm import FakeLLM, extract_json, extract_sql
from t2s.pipeline import Pipeline, public_trace

GOOD = ("SELECT c.name FROM customers c JOIN orders o ON o.customer_id = c.customer_id "
        "WHERE o.status = 'completed' AND o.order_date BETWEEN '2026-10-01' AND '2026-10-31' "
        "GROUP BY c.customer_id HAVING SUM(o.total_amount) > 10000")
BAD = GOOD.replace("o.status = 'completed' AND ", "")


def five(cfg):
    return cfg.with_overrides(candidates=[
        CandidateSpec("gen-model", "direct", 0.0), CandidateSpec("gen-model", "plan", 0.0),
        CandidateSpec("gen-model", "decompose", 0.0), CandidateSpec("gen-model", "direct", 0.7),
        CandidateSpec("other-model", "plan", 0.7)])


def test_extractors():
    assert extract_sql("Plan...\n```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("Sure! SELECT * FROM t;") == "SELECT * FROM t"
    assert extract_json('blah ```json\n{"choice": "A", "reason": "x {y}"}\n```') == \
        {"choice": "A", "reason": "x {y}"}


def test_disagreement_goes_to_judge_with_example(cfg):
    seen_prompts = []

    def responder(messages, model, temp, tag):
        if tag == "judge":
            seen_prompts.append(messages[-1]["content"])
            labels = re.findall(r"Query (\w+):\n```", messages[-1]["content"])
            # pick whichever option is the GOOD query
            good_label = next(l for l in labels
                              if "status" in messages[-1]["content"].split(f"Query {l}:")[1].split("```")[1])
            return json.dumps({"choice": good_label, "confidence": 0.9, "reason": "cancelled order"})
        user = messages[1]["content"]
        if model == "other-model" or "Break the question" in user:
            return f"```sql\n{BAD}\n```"
        return f"```sql\n{GOOD}\n```"

    p = Pipeline(five(cfg), FakeLLM(responder))
    t = asyncio.run(p.ask("shop", "Which customers spent more than 10,000 last month?",
                          "last month refers to October 2026"))
    assert [c["size"] for c in t["clusters"]] == [3, 2]
    assert t["distinguishing"] is not None
    assert "minimal example" in seen_prompts[0]
    assert t["answer"]["sql"] == GOOD
    assert not t["confidence"]["abstained"]
    json.dumps(public_trace(t), default=str)  # serialisable


def test_judge_can_overrule_majority(cfg):
    # 4-1 split: only allowed when override_margin is unlimited (-1).
    def responder(messages, model, temp, tag):
        if tag == "judge":
            c = messages[-1]["content"]
            labels = re.findall(r"Query (\w+):\n```", c)
            good = next(l for l in labels if "status" in c.split(f"Query {l}:")[1].split("```")[1])
            return json.dumps({"choice": good, "confidence": 0.95, "reason": "x"})
        if tag == "clarify":
            return '{"question": "Should cancelled orders count?"}'
        # majority writes the BAD query
        return f"```sql\n{GOOD if model == 'other-model' else BAD}\n```"

    c = five(cfg).with_overrides(judge_override_margin=-1)
    t = asyncio.run(Pipeline(c, FakeLLM(responder)).ask(
        "shop", "Which customers spent more than 10,000 last month?", "October 2026"))
    assert t["answer"]["sql"] == GOOD
    assert t["confidence"]["features"]["agreement"] == 0.2


def test_self_repair_fixes_bad_column(cfg):
    def responder(messages, model, temp, tag):
        if tag == "repair":
            assert "no such column" in messages[-1]["content"]
            return "```sql\nSELECT COUNT(*) FROM customers WHERE city = 'Pune'\n```"
        if tag == "judge":
            return '{"verdict": "pass", "confidence": 0.9}'
        return "```sql\nSELECT COUNT(*) FROM customers WHERE town = 'Pune'\n```"

    c = cfg.with_overrides(candidates=[CandidateSpec("m", "direct", 0.0)])
    t = asyncio.run(Pipeline(c, FakeLLM(responder)).ask("shop", "How many customers live in Pune?"))
    cand = t["candidates"][0]
    assert len(cand["attempts"]) == 2 and cand["error"] is None
    assert t["answer"]["rows"] == [[26]] or t["answer"]["rows"][0][0] > 0


def test_judge_fail_lowers_confidence_and_asks(cfg):
    def responder(messages, model, temp, tag):
        if tag == "judge":
            return '{"verdict": "fail", "confidence": 0.9, "issues": ["ignores status"]}'
        if tag == "clarify":
            return '{"question": "Do you want only completed orders?"}'
        return f"```sql\n{BAD}\n```"

    t = asyncio.run(Pipeline(five(cfg), FakeLLM(responder)).ask("shop", "Which customers spent more than 10,000 last month?"))
    assert t["judge"]["mode"] == "verify" and t["judge"]["choice"] is None
    assert t["confidence"]["score"] < 0.9
    if t["confidence"]["abstained"]:
        assert t["clarifying_question"]


def test_all_candidates_fail_abstains(cfg):
    def responder(messages, model, temp, tag):
        return "```sql\nSELECT nope FROM nowhere\n```"

    c = cfg.with_overrides(max_repairs=1)
    t = asyncio.run(Pipeline(c, FakeLLM(responder)).ask("shop", "anything?"))
    assert t["answer"] is None and t["confidence"]["abstained"]


def test_eval_harness_end_to_end(cfg, tmp_path, shop_db):
    from eval.run import run_eval

    root = Path(__file__).resolve().parents[1]
    qs = json.loads((root / "data/demo/questions.json").read_text())
    gold = {q["question"]: q["SQL"] for q in qs}

    def responder(messages, model, temp, tag):
        if tag == "judge":
            c = messages[-1]["content"]
            if "Candidate queries" in c:
                return json.dumps({"choice": re.findall(r"Query (\w+):\n```", c)[0], "confidence": 0.7})
            return '{"verdict": "pass", "confidence": 0.8}'
        if tag == "clarify":
            return '{"question": "?"}'
        q = re.search(r"Question: (.*)", messages[1]["content"]).group(1).strip()
        sql = gold[q]
        if "Break the question" in messages[1]["content"]:
            sql = sql.replace("COUNT(DISTINCT", "COUNT(")
        return f"```sql\n{sql}\n```"

    db_root = tmp_path / "dbs"
    (db_root / "shop").mkdir(parents=True)
    (db_root / "shop" / "shop.sqlite").write_bytes(shop_db.read_bytes())
    cfg.databases.clear()
    s = asyncio.run(run_eval(five(cfg), str(root / "data/demo/questions.json"), str(db_root),
                             tmp_path / "run", llm=FakeLLM(responder), quiet=True))
    assert s["headline"]["questions"] == 12
    assert s["headline"]["execution_accuracy"] == 1.0
    assert (tmp_path / "run" / "report.md").exists()
    # resumable: a second run skips everything
    s2 = asyncio.run(run_eval(five(cfg), str(root / "data/demo/questions.json"), str(db_root),
                              tmp_path / "run", llm=FakeLLM(responder), quiet=True))
    assert s2["headline"]["questions"] == 12


def test_served_model_is_recorded(cfg):
    class Routed(FakeLLM):
        async def chat(self, messages, model, temperature=0.0, max_tokens=1024, tag=""):
            r = await super().chat(messages, model, temperature, max_tokens, tag)
            r.model = "provider/real-model" if model == "auto" else model
            return r

    def responder(messages, model, temp, tag):
        if tag == "judge":
            return '{"verdict": "pass", "confidence": 0.9}'
        return "```sql\nSELECT COUNT(*) FROM customers\n```"

    c = cfg.with_overrides(candidates=[CandidateSpec("auto", "direct", 0.0)])
    t = asyncio.run(Pipeline(c, Routed(responder)).ask("shop", "How many customers?"))
    assert t["candidates"][0]["model"] == "auto"
    assert t["candidates"][0]["served_model"] == "provider/real-model"


def test_judge_failure_does_not_lose_the_question(cfg):
    def responder(messages, model, temp, tag):
        if tag == "judge":
            raise RuntimeError("429 Too Many Requests")
        return "```sql\nSELECT COUNT(*) FROM customers WHERE city = 'Pune'\n```"

    t = asyncio.run(Pipeline(five(cfg), FakeLLM(responder)).ask("shop", "How many customers live in Pune?"))
    assert t["answer"] is not None and t["judge"] is None
    assert "429" in t["judge_error"]


def test_all_gateway_failures_are_retried_not_scored(cfg, tmp_path, shop_db):
    from eval.run import run_eval

    root = Path(__file__).resolve().parents[1]
    db_root = tmp_path / "dbs"
    (db_root / "shop").mkdir(parents=True)
    (db_root / "shop" / "shop.sqlite").write_bytes(shop_db.read_bytes())
    cfg.databases.clear()

    def down(messages, model, temp, tag):
        raise RuntimeError("429 Too Many Requests")

    s = asyncio.run(run_eval(five(cfg), str(root / "data/demo/questions.json"), str(db_root),
                             tmp_path / "run", limit=3, llm=FakeLLM(down), quiet=True))
    assert s["headline"]["questions"] == 0 and len(s["infra_failures_pending"]) == 3

    gold = {q["question"]: q["SQL"] for q in json.loads((root / "data/demo/questions.json").read_text())}

    def up(messages, model, temp, tag):
        if tag == "judge":
            return '{"verdict": "pass", "confidence": 0.9}'
        q = re.search(r"Question: (.*)", messages[1]["content"]).group(1).strip()
        return f"```sql\n{gold[q]}\n```"

    s = asyncio.run(run_eval(five(cfg), str(root / "data/demo/questions.json"), str(db_root),
                             tmp_path / "run", limit=3, llm=FakeLLM(up), quiet=True))
    assert s["headline"]["questions"] == 3 and s["infra_failures_pending"] == []


def _split_vote(cfg, margin, bad_is_majority):
    """3 candidates write GOOD, 2 write BAD (or the reverse); the judge always picks BAD."""
    def responder(messages, model, temp, tag):
        if tag == "judge":
            c = messages[-1]["content"]
            labels = re.findall(r"Query (\w+):\n```", c)
            bad = next((l for l in labels
                        if "status" not in c.split(f"Query {l}:")[1].split("```")[1]), labels[0])
            return json.dumps({"choice": bad, "confidence": 0.99, "reason": "x"})
        if tag == "clarify":
            return '{"question": "?"}'
        user = messages[1]["content"]
        minority = "Break the question" in user or ("plan" in user.lower() and temp > 0)
        return f"```sql\n{BAD if minority != bad_is_majority else GOOD}\n```"

    c = five(cfg).with_overrides(judge_override_margin=margin)
    return asyncio.run(Pipeline(c, FakeLLM(responder)).ask(
        "shop", "Which customers spent more than 10,000 last month?", "October 2026"))


def test_judge_cannot_overrule_a_clear_majority(cfg):
    t = _split_vote(cfg, margin=0, bad_is_majority=False)   # 3-2 split, ties only
    assert t["answer"]["sql"] == GOOD                        # majority stands
    assert t["judge_blocked"]["would_have_changed_answer"] is True
    assert t["confidence"]["features"]["judge_support"] == 0.0


def test_judge_decides_when_allowed(cfg):
    t = _split_vote(cfg, margin=1, bad_is_majority=False)   # 3-2 is within the margin
    assert t["answer"]["sql"] == BAD                         # judge wins
    assert t["judge_blocked"]["would_have_changed_answer"] is False


def test_judge_agreeing_with_the_majority_is_never_blocked(cfg):
    t = _split_vote(cfg, margin=0, bad_is_majority=True)    # judge picks the majority
    assert t["answer"]["sql"] == BAD
    assert t["judge_blocked"]["would_have_changed_answer"] is False


def _three_way(cfg, judge_choice, **over):
    """Five candidates land in three clusters; the judge picks `judge_choice`."""
    THIRD = GOOD.replace("> 10000", "> 15000")
    asked = {}

    def responder(messages, model, temp, tag):
        if tag == "judge":
            c = messages[-1]["content"]
            labels = re.findall(r"Query (\w+):\n```", c)
            asked.setdefault("orders", []).append(labels)
            return json.dumps({"choice": judge_choice(labels), "confidence": 0.9, "reason": "x"})
        if tag == "clarify":
            return '{"question": "?"}'
        user = messages[1]["content"]
        if "Break the question" in user:
            return f"```sql\n{THIRD}\n```"
        if "plan" in user.lower() and temp > 0:
            return f"```sql\n{BAD}\n```"
        return f"```sql\n{GOOD}\n```"

    c = five(cfg).with_overrides(judge_override_margin=-1, **over)
    t = asyncio.run(Pipeline(c, FakeLLM(responder)).ask(
        "shop", "Which customers spent more than 10,000 last month?", "October 2026"))
    return t, asked


def test_judge_can_reach_a_third_cluster(cfg):
    # The third cluster is never the largest, so top-two judging could not pick it.
    t, asked = _three_way(cfg, lambda labels: labels[-1])
    assert len(t["clusters"]) == 3
    assert len(asked["orders"][0]) == 3            # all three shown to the judge
    assert t["judge"]["n_options"] == 3
    assert t["answer"]["cluster"] == 2             # the smallest cluster won


def test_top_two_only_cannot_reach_it(cfg):
    t, _ = _three_way(cfg, lambda labels: labels[-1], judge_max_options=2)
    assert t["judge"]["n_options"] == 2
    assert t["answer"]["cluster"] in (0, 1)        # cluster 2 is unreachable


def test_order_bias_check_drops_an_unstable_verdict(cfg):
    # Always picking whatever is listed first is pure position bias: both asks disagree.
    t, asked = _three_way(cfg, lambda labels: labels[0], judge_check_order_bias=True)
    assert len(asked["orders"]) == 2
    assert asked["orders"][1] == asked["orders"][0][::-1]   # second ask is reversed
    assert t["judge"]["flipped"] is True
    assert t["judge"]["choice"] is None                     # verdict dropped
    assert t["answer"]["cluster"] == 0                      # majority stands


def test_order_bias_check_keeps_a_stable_verdict(cfg):
    stable = {}

    def choose(labels):
        stable.setdefault("pick", labels[-1])   # same label whichever order it appears in
        return stable["pick"]

    t, asked = _three_way(cfg, choose, judge_check_order_bias=True)
    assert len(asked["orders"]) == 2
    assert t["judge"]["flipped"] is False
    assert t["judge"]["choice"] is not None
