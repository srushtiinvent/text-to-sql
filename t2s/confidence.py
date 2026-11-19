"""Combine verification signals into one confidence score and decide whether to abstain.

Starts with hand-set weights. `python -m eval.calibrate` fits a logistic
regression on a held-out split of an eval run and writes learned weights to
`confidence.weights_path`, which this module then loads.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

FEATURES = ["agreement", "margin", "valid_frac", "judge_support", "judge_confidence",
            "example_found"]

DEFAULT_WEIGHTS = {"bias": -2.0, "agreement": 2.5, "margin": 1.0, "valid_frac": 0.5,
                   "judge_support": 2.0, "judge_confidence": 0.5, "example_found": -0.3}


def features(n_candidates: int, clusters, chosen_cluster: int | None, judge,
             judge_blocked: bool = False) -> dict[str, float]:
    n_valid = sum(c.size for c in clusters)
    if chosen_cluster is None or not clusters:
        return {f: 0.0 for f in FEATURES}
    chosen = clusters[chosen_cluster]
    best_other = max((c.size for c in clusters if c.id != chosen_cluster), default=0)
    if judge is None:
        support, jconf = 0.5, 0.5
    elif judge_blocked:  # the judge disagreed with the answer we are giving
        support, jconf = 0.0, judge.confidence
    else:
        support = 1.0 if judge.choice is not None else 0.0
        jconf = judge.confidence if judge.choice is not None else 1 - judge.confidence
    return {
        "agreement": chosen.size / n_valid if n_valid else 0.0,
        "margin": (chosen.size - best_other) / max(n_candidates, 1),
        "valid_frac": n_valid / max(n_candidates, 1),
        "judge_support": support,
        "judge_confidence": jconf,
        "example_found": 1.0 if (judge is not None and judge.used_example) else 0.0,
    }


def load_weights(path: str | Path | None) -> dict[str, float]:
    if path and Path(path).exists():
        try:
            data = json.loads(Path(path).read_text())
            return {**DEFAULT_WEIGHTS, **data.get("weights", {})}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_WEIGHTS)


def score(feats: dict[str, float], weights: dict[str, float]) -> float:
    if not any(feats.values()):
        return 0.0
    z = weights.get("bias", 0.0) + sum(weights.get(f, 0.0) * feats.get(f, 0.0) for f in FEATURES)
    return 1.0 / (1.0 + math.exp(-z))
