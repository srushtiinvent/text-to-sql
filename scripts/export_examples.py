"""Pick recorded traces for the frontend's "Recorded examples" tab.

    python scripts/export_examples.py runs/demo --ids 1 9 11
    python scripts/export_examples.py trace.json trace2.json

Each argument is either a run folder (uses its traces.jsonl, filtered by --ids)
or a JSON file holding one trace saved from /api/ask. Writes web/public/examples.json.
Recorded examples replay instantly in the demo and use no model quota.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

KEEP = ["id", "database", "question", "evidence", "dialect", "retrieval", "candidates", "clusters",
        "distinguishing", "judge", "judge_error", "confidence", "clarifying_question", "answer",
        "usage", "timings_ms"]


def slim(trace: dict) -> dict:
    t = {k: trace.get(k) for k in KEEP}
    if t.get("retrieval"):
        t["retrieval"] = {k: v for k, v in t["retrieval"].items() if k != "schema_text"}
    for c in t.get("candidates") or []:
        if c.get("result"):
            c["result"]["rows"] = c["result"]["rows"][:5]
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--ids", nargs="*", type=int, help="question ids to take from run folders")
    ap.add_argument("--out", default="web/public/examples.json")
    args = ap.parse_args()
    out = []
    for src in map(Path, args.sources):
        if src.is_dir():
            for line in (src / "traces.jsonl").read_text(encoding="utf-8").splitlines():
                t = json.loads(line)
                if not args.ids or t.get("question_id") in args.ids:
                    out.append(slim(t))
        else:
            out.append(slim(json.loads(src.read_text(encoding="utf-8"))))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"wrote {len(out)} examples to {args.out}")


if __name__ == "__main__":
    main()
