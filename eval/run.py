"""Run the pipeline over a BIRD-format dataset and score it.

    python -m eval.run --dataset data/demo/questions.json --db-root data/demo/databases --name demo
    python -m eval.run --dataset bird/dev.json --db-root bird/dev_databases --limit 300 --name dev300

Outputs in runs/<name>/:
    results.jsonl   one scored record per question (resumable: finished ids are skipped)
    traces.jsonl    full pipeline traces (what the frontend shows)
    summary.json    all metrics (see eval/metrics.py)
    report.md       human-readable report + charts

A single run already gives three baselines for free: the first candidate alone,
majority vote over clusters, and the full verified pipeline.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from t2s.config import load_config
from t2s.execute.base import ExecutionError
from t2s.execute.sqlite_exec import SQLiteExecutor
from t2s.llm import GatewayLLM
from t2s.pipeline import Pipeline, public_trace
from t2s.verify.cluster import set_equal
from t2s.verify.distinguish import referenced_tables

from .bird import load_dataset, register_databases, sample
from .metrics import summarize
from .report import write_report


def parse_overrides(pairs: list[str]) -> dict:
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


async def run_eval(cfg, dataset: str, db_root: str, out_dir: Path, *, limit: int | None = None,
                   seed: int = 7, llm=None, quiet: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    examples = sample(load_dataset(dataset), limit, seed)
    register_databases(cfg, db_root, {e.db_id for e in examples})
    (out_dir / "sample_ids.json").write_text(json.dumps([e.question_id for e in examples]))

    results_path, traces_path = out_dir / "results.jsonl", out_dir / "traces.jsonl"
    done = set()
    if results_path.exists():
        done = {json.loads(l)["question_id"] for l in results_path.read_text().splitlines() if l.strip()}
    todo = [e for e in examples if e.question_id not in done]

    owns_llm = llm is None
    llm = llm or GatewayLLM(cfg.gateway_url, cfg.gateway_key, cfg.gateway_timeout_s,
                            cfg.gateway_max_retries)
    pipeline = Pipeline(cfg, llm)
    gold_execs: dict[str, SQLiteExecutor] = {}
    sem = asyncio.Semaphore(cfg.eval_concurrency)
    lock = asyncio.Lock()
    started = time.perf_counter()
    finished = 0

    async def one(ex):
        nonlocal finished
        async with sem:
            record = await score_example(pipeline, cfg, ex, gold_execs)
        async with lock:
            trace = record.pop("_trace", None)
            with results_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
            if trace:
                with traces_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(trace, default=str) + "\n")
            finished += 1
            if not quiet:
                mark = "✓" if record.get("correct") else ("?" if record.get("abstained") else "✗")
                print(f"[{finished}/{len(todo)}] {mark} q{ex.question_id} ({ex.db_id}) "
                      f"conf={record.get('confidence', 0):.2f} "
                      f"{time.perf_counter() - started:.0f}s", flush=True)

    try:
        await asyncio.gather(*(one(e) for e in todo))
    finally:
        if owns_llm:
            await llm.aclose()

    records = [json.loads(l) for l in results_path.read_text().splitlines() if l.strip()]
    summary = summarize(records, threshold=cfg.confidence_threshold)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    write_report(summary, records, out_dir)
    return summary


async def score_example(pipeline, cfg, ex, gold_execs) -> dict:
    base = {"question_id": ex.question_id, "db_id": ex.db_id, "difficulty": ex.difficulty,
            "question": ex.question, "evidence": ex.evidence, "gold_sql": ex.gold_sql}
    spec = cfg.databases[ex.db_id]
    if ex.db_id not in gold_execs:  # gold queries get a longer timeout and no static guard
        gold_execs[ex.db_id] = SQLiteExecutor(cfg.resolve(spec.path), timeout_s=60,
                                              row_limit=10**7, static_guard=False)
    try:
        gold = await asyncio.to_thread(gold_execs[ex.db_id].run, ex.gold_sql)
    except ExecutionError as e:
        return {**base, "skipped": f"gold query failed: {e}"}

    try:
        trace = await pipeline.ask(ex.db_id, ex.question, ex.evidence)
    except Exception as e:  # keep the run going; failures are counted in the summary
        return {**base, "error": f"{type(e).__name__}: {e}", "correct": False,
                "abstained": True, "confidence": 0.0}

    internal = trace["_internal"]
    cands, clusters, chosen, decision = (internal["candidates"], internal["clusters"],
                                         internal["chosen"], internal["decision"])
    by_id = {c.id: c for c in cands}

    def full_result(c):
        # previews are truncated to row_limit; re-run without a limit for exact scoring
        if c.result is not None and not c.result.truncated:
            return c.result
        try:
            return gold_execs[ex.db_id].run(c.sql)
        except ExecutionError:
            return None

    cand_correct = {}
    for c in cands:
        r = full_result(c) if c.ok else None
        cand_correct[c.id] = bool(r is not None and set_equal(r, gold))
    cluster_correct = [cand_correct[cl.representative] for cl in clusters]
    correct = bool(chosen is not None and cluster_correct[chosen])

    judge = None
    if decision is not None:
        if decision.mode == "pick":
            reps = [by_id[clusters[0].representative], by_id[clusters[1].representative]]
            opts_correct = {r.label: cand_correct[r.id] for r in reps}
            opt_models = {r.label: sorted({by_id[m].model for m in clusters[r.cluster].members})
                          for r in reps}
            judge_correct = (opts_correct.get(decision.choice, False) if any(opts_correct.values())
                             else decision.choice is None)
        else:
            rep = by_id[clusters[0].representative]
            opts_correct = {rep.label: cand_correct[rep.id]}
            opt_models = {rep.label: sorted({by_id[m].model for m in clusters[0].members})}
            judge_correct = (decision.choice is not None) == cand_correct[rep.id]
        judge = {"mode": decision.mode, "choice": decision.choice, "model": decision.model,
                 "confidence": decision.confidence, "used_example": decision.used_example,
                 "options_correct": opts_correct, "option_models": opt_models,
                 "correct": judge_correct}

    schema = internal["schema"]
    gold_tables = referenced_tables(ex.gold_sql, schema)
    retrieved = set(trace["retrieval"]["tables"])
    record = {
        **base,
        "correct": correct,
        "abstained": trace["confidence"]["abstained"],
        "confidence": trace["confidence"]["score"],
        "features": trace["confidence"]["features"],
        "pred_sql": trace["answer"]["sql"] if trace["answer"] else None,
        "n_candidates": len(cands),
        "n_valid": sum(1 for c in cands if c.ok),
        "candidate_correct": [cand_correct[c.id] for c in cands],
        "candidate_models": [c.model for c in cands],
        "candidate_repaired": [len(c.attempts) > 1 for c in cands],
        "cluster_sizes": [cl.size for cl in clusters],
        "cluster_correct": cluster_correct,
        "majority_correct": bool(cluster_correct and cluster_correct[0]),
        "first_candidate_correct": cand_correct.get(0, False),
        "any_candidate_correct": any(cand_correct.values()),
        "judge": judge,
        "distinguishing_found": bool(trace.get("distinguishing")),
        "distinguishing_rows": (trace.get("distinguishing") or {}).get("rows_total"),
        "retrieval": {"mode": trace["retrieval"]["mode"], "tables": sorted(retrieved),
                      "gold_tables": gold_tables,
                      "recall_hit": set(gold_tables) <= retrieved},
        "usage": trace["usage"],
        "timings_ms": trace["timings_ms"],
        "_trace": {**public_trace(trace), "question_id": ex.question_id, "correct": correct,
                   "gold_sql": ex.gold_sql},
    }
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--db-root", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--name", default=time.strftime("run-%Y%m%d-%H%M%S"))
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a config value, e.g. --set judge=false")
    args = ap.parse_args()
    cfg = load_config(args.config).with_overrides(**parse_overrides(args.set))
    summary = asyncio.run(run_eval(cfg, args.dataset, args.db_root, Path("runs") / args.name,
                                   limit=args.limit, seed=args.seed))
    h = summary["headline"]
    print(json.dumps(h, indent=2))
    print(f"report: runs/{args.name}/report.md")


if __name__ == "__main__":
    main()
