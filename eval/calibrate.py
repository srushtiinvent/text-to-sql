"""Learn confidence weights from an eval run, honestly.

    python -m eval.calibrate runs/dev300 --target-accuracy 0.9

Splits the questions in two by a hash of question_id. Fits a logistic regression
on one half, chooses the threshold on that half, and reports coverage/accuracy on
the *other* half. Choosing the threshold on the same data you report is the most
common way this kind of chart ends up overstated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from t2s.confidence import FEATURES


def _split(qid) -> int:
    return int(hashlib.md5(str(qid).encode()).hexdigest(), 16) % 2


def _matrix(records):
    X = np.array([[r["features"].get(f, 0.0) for f in FEATURES] for r in records], dtype=float)
    y = np.array([1 if r["correct"] else 0 for r in records])
    return X, y


def choose_threshold(probs, y, target: float) -> float:
    """Lowest threshold whose answered set still meets the target accuracy (max coverage)."""
    order = np.argsort(-probs)
    best = 1.0
    correct = 0
    for k, i in enumerate(order, 1):
        correct += y[i]
        if correct / k >= target:
            best = float(probs[i])
    return best


def evaluate(probs, y, thr: float) -> dict:
    mask = probs >= thr
    return {"coverage": round(float(mask.mean()), 4) if len(y) else 0.0,
            "accuracy": round(float(y[mask].mean()), 4) if mask.any() else None,
            "n": int(len(y))}


def main():
    from sklearn.linear_model import LogisticRegression

    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--target-accuracy", type=float, default=0.9)
    ap.add_argument("--out", default="data/confidence_weights.json")
    args = ap.parse_args()

    recs = [json.loads(l) for l in (Path(args.run_dir) / "results.jsonl").read_text().splitlines()
            if l.strip()]
    recs = [r for r in recs if "features" in r and "skipped" not in r]
    fit = [r for r in recs if _split(r["question_id"]) == 0]
    hold = [r for r in recs if _split(r["question_id"]) == 1]
    Xf, yf = _matrix(fit)
    Xh, yh = _matrix(hold)
    if len(set(yf)) < 2:
        raise SystemExit("need both correct and wrong answers in the fit split; run more questions")

    model = LogisticRegression(C=1.0, max_iter=1000).fit(Xf, yf)
    pf, ph = model.predict_proba(Xf)[:, 1], model.predict_proba(Xh)[:, 1]
    thr = choose_threshold(pf, yf, args.target_accuracy)
    out = {
        "weights": {"bias": float(model.intercept_[0]),
                    **{f: float(w) for f, w in zip(FEATURES, model.coef_[0])}},
        "recommended_threshold": round(thr, 4),
        "target_accuracy": args.target_accuracy,
        "fit": evaluate(pf, yf, thr),
        "holdout": evaluate(ph, yh, thr),
        "source_run": str(args.run_dir),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    print(f"\nSet [confidence] threshold = {out['recommended_threshold']} in config.toml, and report "
          f"the holdout numbers, not the fit numbers.")


if __name__ == "__main__":
    main()
