"""Turn per-question records into the numbers that go in the README.

Headline metrics
  execution_accuracy     final answer matches gold rows (BIRD's set comparison), answering everything
  first_candidate_acc    baseline: a single direct prompt, no verification
  majority_vote_acc      baseline: biggest result cluster wins, no judge
  oracle_acc             some candidate was correct: the ceiling for any selection method
  selection_efficiency   execution_accuracy / oracle_acc: how much of the ceiling selection captures
  coverage/accuracy @ threshold, AURC (area under the risk-coverage curve, lower is better)
"""
from __future__ import annotations

import statistics
from collections import defaultdict


def _mean(xs) -> float:
    xs = list(xs)
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def _mo(xs):
    """Mean, or None when there is nothing to average (so reports show a dash, not 0%)."""
    xs = list(xs)
    return round(sum(xs) / len(xs), 4) if xs else None


def _pct(xs, q) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    i = min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))
    return round(xs[i], 1)


def risk_coverage(records) -> tuple[list[dict], float]:
    """Sort by confidence (high first); accuracy of the top-k answered for every k."""
    ranked = sorted(records, key=lambda r: -r.get("confidence", 0.0))
    points, correct = [], 0
    for k, r in enumerate(ranked, 1):
        correct += bool(r.get("correct"))
        points.append({"coverage": round(k / len(ranked), 4), "accuracy": round(correct / k, 4),
                       "threshold": r.get("confidence", 0.0)})
    aurc = _mean(1 - p["accuracy"] for p in points)
    return points, aurc


def judge_stats(records) -> dict:
    decisions = [r["judge"] for r in records if r.get("judge")]
    out = {"decisions": len(decisions)}
    for mode in ("pick", "verify"):
        ds = [d for d in decisions if d["mode"] == mode]
        out[mode] = {"n": len(ds), "accuracy": _mo(d["correct"] for d in ds)}
    picks = [d for d in decisions if d["mode"] == "pick"]
    out["pick"]["with_example"] = _mo(d["correct"] for d in picks if d["used_example"])
    out["pick"]["without_example"] = _mo(d["correct"] for d in picks if not d["used_example"])
    # verify mode as a classifier: does "fail" catch wrong unanimous answers?
    ver = [d for d in decisions if d["mode"] == "verify"]
    wrong = [d for d in ver if not any(d["options_correct"].values())]
    right = [d for d in ver if any(d["options_correct"].values())]
    out["verify"]["catches_wrong_rate"] = _mo(d["choice"] is None for d in wrong)
    out["verify"]["false_alarm_rate"] = _mo(d["choice"] is None for d in right)

    # Self-preference: among picks where exactly one option contains a candidate written by the
    # judge's own model, how often does the judge choose that option, and how often when it is wrong?
    own_total = own_picked = own_wrong_total = own_wrong_picked = 0
    for d in picks:
        jm = d.get("model")
        own = [label for label, models in d["option_models"].items() if jm in models]
        if len(own) != 1:
            continue
        own_total += 1
        own_picked += d["choice"] == own[0]
        if not d["options_correct"].get(own[0], False):
            own_wrong_total += 1
            own_wrong_picked += d["choice"] == own[0]
    out["order_bias"] = {
        "checked": sum(1 for d in picks if d.get("flipped") is not None),
        "flipped_rate": _mo(bool(d.get("flipped")) for d in picks),
    }
    out["self_preference"] = {
        "comparable_decisions": own_total,
        "picks_own_model_rate": round(own_picked / own_total, 4) if own_total else None,
        "picks_own_when_wrong_rate": round(own_wrong_picked / own_wrong_total, 4) if own_wrong_total else None,
    }
    return out


def summarize(records: list[dict], threshold: float = 0.5) -> dict:
    scored = [r for r in records if "skipped" not in r]
    errors = [r for r in scored if r.get("error")]
    n = len(scored)
    answered = [r for r in scored if r.get("confidence", 0) >= threshold and r.get("pred_sql")]
    points, aurc = risk_coverage(scored)

    by_diff = defaultdict(list)
    for r in scored:
        by_diff[r["difficulty"]].append(r)

    oracle = _mean(r.get("any_candidate_correct", False) for r in scored)
    ex = _mean(r.get("correct", False) for r in scored)
    retrieved = [r for r in scored if r.get("retrieval", {}).get("mode") == "retrieved"]
    usage = [r["usage"] for r in scored if r.get("usage")]
    totals = [r["timings_ms"]["total"] for r in scored if r.get("timings_ms")]

    headline = {
        "questions": n,
        "execution_accuracy": ex,
        "first_candidate_accuracy": _mean(r.get("first_candidate_correct", False) for r in scored),
        "majority_vote_accuracy": _mean(r.get("majority_correct", False) for r in scored),
        "oracle_accuracy": oracle,
        "selection_efficiency": round(ex / oracle, 4) if oracle else 0.0,
        "threshold": threshold,
        "coverage_at_threshold": round(len(answered) / n, 4) if n else 0.0,
        "accuracy_at_threshold": _mean(r["correct"] for r in answered),
        "aurc": aurc,
    }
    return {
        "headline": headline,
        "by_difficulty": {d: {"n": len(rs), "execution_accuracy": _mean(r.get("correct", False) for r in rs),
                              "oracle_accuracy": _mean(r.get("any_candidate_correct", False) for r in rs)}
                          for d, rs in sorted(by_diff.items())},
        "retrieval": {
            "questions_using_retrieval": len(retrieved),
            "table_recall": _mean(r["retrieval"]["recall_hit"] for r in retrieved) if retrieved else None,
            "avg_tables_retrieved": _mean(len(r["retrieval"]["tables"]) for r in retrieved) if retrieved else None,
        },
        "generation": {
            "avg_valid_candidates": _mean(r.get("n_valid", 0) for r in scored),
            "avg_clusters": _mean(len(r.get("cluster_sizes", [])) for r in scored),
            "unanimous_rate": _mean(len(r.get("cluster_sizes", [])) == 1 for r in scored),
            "repair_rate": _mean(any(r.get("candidate_repaired", [])) for r in scored),
            # Infrastructure failures (gateway 429s, timeouts) are not model mistakes; report them apart.
            "llm_failure_rate": _mean(k == "llm" for r in scored for k in r.get("candidate_error_kinds", [])),
            "first_candidate_llm_failures": sum(1 for r in scored
                                                if (r.get("candidate_error_kinds") or [None])[0] == "llm"),
        },
        "selection": {
            # A correct cluster ranked below judge_max_options can never be chosen.
            "answer_outside_judge_options": sum(
                1 for r in scored
                if r.get("correct_cluster_index") is not None
                and r.get("judge", {}) and (r["judge"].get("n_options") or 2) <= r["correct_cluster_index"]),
            "correct_in_minority_cluster": sum(
                1 for r in scored if (r.get("correct_cluster_index") or 0) > 0),
        },
        "verification": {
            "distinguishing_found_rate": _mean(r.get("distinguishing_found", False)
                                               for r in scored if len(r.get("cluster_sizes", [])) >= 2),
            "median_distinguishing_rows": (statistics.median([r["distinguishing_rows"] for r in scored
                                                              if r.get("distinguishing_rows")])
                                           if any(r.get("distinguishing_rows") for r in scored) else None),
            "judge": judge_stats(scored),
        },
        "cost": {
            "avg_llm_calls": _mean(u["calls"] for u in usage),
            "avg_prompt_tokens": _mean(u["prompt_tokens"] for u in usage),
            "avg_completion_tokens": _mean(u["completion_tokens"] for u in usage),
            "latency_p50_ms": _pct(totals, 0.5),
            "latency_p95_ms": _pct(totals, 0.95),
        },
        "errors": len(errors),
        "skipped": len(records) - n,
        "risk_coverage": points,
    }
