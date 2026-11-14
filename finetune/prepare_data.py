"""Build a chat-format fine-tuning set from BIRD *train* (never dev).

    python -m finetune.prepare_data --dataset bird/train/train.json \
        --db-root bird/train/train_databases --out data/finetune/train.jsonl \
        --failures runs/train-sample/results.jsonl

Each example uses exactly the prompt the pipeline sends at inference time
(same retrieval, same schema rendering, same "direct" template), so the model
learns the format it will see. Questions the API models got wrong in an eval
run over *train* (--failures) are repeated, so training focuses on hard cases.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path

from eval.bird import load_dataset, register_databases
from t2s.config import load_config
from t2s.execute.base import ExecutionError
from t2s.generate.prompts import build_messages
from t2s.pipeline import Pipeline


async def build(args):
    if "dev" in Path(args.dataset).name.lower() and not args.i_know_this_is_dev:
        raise SystemExit("Refusing to train on a dev set: your dev numbers would be meaningless.")
    cfg = load_config(args.config)
    examples = load_dataset(args.dataset)
    rng = random.Random(args.seed)
    rng.shuffle(examples)
    if args.max_examples:
        examples = examples[: args.max_examples]
    register_databases(cfg, args.db_root, {e.db_id for e in examples})
    pipeline = Pipeline(cfg, llm=None)  # retrieval only; no LLM calls are made

    failed_ids: set[int] = set()
    if args.failures:
        for line in Path(args.failures).read_text().splitlines():
            r = json.loads(line)
            if not r.get("correct"):
                failed_ids.add(r["question_id"])

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    with out_path.open("w", encoding="utf-8") as f:
        for i, ex in enumerate(examples, 1):
            ctx = await pipeline.context(ex.db_id)
            try:  # BIRD train has a few broken gold queries; do not teach those
                await asyncio.to_thread(ctx.executor.run, ex.gold_sql)
            except ExecutionError:
                skipped += 1
                continue
            retrieval = await ctx.retriever.retrieve(ex.question, ex.evidence)
            messages = build_messages("direct", dialect=ctx.dialect, schema_text=retrieval.schema_text,
                                      question=ex.question, evidence=ex.evidence,
                                      hints=retrieval.hints_text())
            messages.append({"role": "assistant", "content": f"```sql\n{ex.gold_sql.strip()}\n```"})
            repeats = args.failure_weight if ex.question_id in failed_ids else 1
            for _ in range(repeats):
                f.write(json.dumps({"messages": messages, "question_id": ex.question_id,
                                    "db_id": ex.db_id}) + "\n")
                written += 1
            if i % 200 == 0:
                print(f"{i}/{len(examples)} processed")
    print(f"wrote {written} rows to {out_path} (skipped {skipped} broken gold queries, "
          f"{len(failed_ids)} hard examples upweighted x{args.failure_weight})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--db-root", required=True)
    ap.add_argument("--out", default="data/finetune/train.jsonl")
    ap.add_argument("--failures", help="results.jsonl of an eval run over the TRAIN split")
    ap.add_argument("--failure-weight", type=int, default=2)
    ap.add_argument("--max-examples", type=int)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--i-know-this-is-dev", action="store_true", help=argparse.SUPPRESS)
    asyncio.run(build(ap.parse_args()))


if __name__ == "__main__":
    main()
