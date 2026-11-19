"""Ablation study: switch off one component at a time and measure what it was worth.

    python -m eval.ablate --dataset bird/dev.json --db-root bird/dev_databases --limit 200 --name abl200

All variants run on the same fixed question sample. Your gateway's cache makes
repeated identical generation calls free, so later variants are much cheaper.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from t2s.config import load_config

from .run import run_eval


def variants(cfg) -> dict[str, dict]:
    return {
        "full": {},
        "no_judge (majority vote)": {"judge": False, "distinguish": False},
        "judge_without_example": {"distinguish": False},
        "judge_may_overrule_any_majority": {"judge_override_margin": -1},
        "judge_on_ties_only": {"judge_override_margin": 0},
        "judge_top_two_only": {"judge_max_options": 2},
        "judge_order_bias_checked": {"judge_check_order_bias": True},
        "single_candidate": {"candidates": cfg.candidates[:1], "judge": False, "distinguish": False},
        "no_self_repair": {"max_repairs": 0},
        "no_value_hints": {"value_match_threshold": 1.01},
        "no_schema_retrieval": {"full_schema_column_threshold": 10**9},
    }


async def main_async(args):
    base = load_config(args.config)
    rows = []
    for name, overrides in variants(base).items():
        if args.only and name not in args.only:
            continue
        cfg = base.with_overrides(**overrides)
        slug = name.split(" ")[0]
        out = Path("runs") / args.name / slug
        print(f"\n=== {name} ===")
        s = await run_eval(cfg, args.dataset, args.db_root, out, limit=args.limit, seed=args.seed,
                           quiet=True)
        h = s["headline"]
        rows.append((name, h["execution_accuracy"], h["coverage_at_threshold"],
                     h["accuracy_at_threshold"], s["cost"]["avg_llm_calls"]))
        print(json.dumps(h, indent=2))

    full = next((r[1] for r in rows if r[0] == "full"), None)
    lines = ["| Variant | Accuracy | Δ vs full | Coverage @thr | Accuracy @thr | LLM calls/q |",
             "|---|---|---|---|---|---|"]
    for name, ex, cov, acc, calls in rows:
        delta = "—" if full is None or name == "full" else f"{100 * (ex - full):+.1f} pts"
        lines.append(f"| {name} | {100 * ex:.1f}% | {delta} | {100 * cov:.1f}% | {100 * acc:.1f}% | {calls} |")
    table = "\n".join(lines)
    (Path("runs") / args.name / "ablation.md").write_text(table + "\n")
    print("\n" + table)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--db-root", required=True)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--name", default="ablation")
    ap.add_argument("--only", nargs="*", help="run only these variant names")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
