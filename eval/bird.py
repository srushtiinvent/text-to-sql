"""Load BIRD-format datasets (dev.json / mini_dev) and register their SQLite databases.

Expected layout (the official BIRD dev download):
    dev.json
    dev_databases/<db_id>/<db_id>.sqlite
    dev_databases/<db_id>/database_description/<table>.csv
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from t2s.config import Config, DatabaseSpec


@dataclass
class Example:
    question_id: int
    db_id: str
    question: str
    evidence: str
    gold_sql: str
    difficulty: str


def load_dataset(path: str | Path) -> list[Example]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for i, r in enumerate(raw):
        out.append(Example(
            question_id=int(r.get("question_id", i)),
            db_id=r["db_id"],
            question=r["question"],
            evidence=r.get("evidence", "") or "",
            gold_sql=r.get("SQL") or r.get("sql") or r.get("query"),
            difficulty=r.get("difficulty", "unknown"),
        ))
    return out


def sample(examples: list[Example], limit: int | None, seed: int) -> list[Example]:
    """Deterministic, difficulty-stratified sample so small runs stay representative."""
    if not limit or limit >= len(examples):
        return examples
    rng = random.Random(seed)
    by_diff: dict[str, list[Example]] = {}
    for e in examples:
        by_diff.setdefault(e.difficulty, []).append(e)
    picked: list[Example] = []
    for diff, group in sorted(by_diff.items()):
        k = max(1, round(limit * len(group) / len(examples)))
        picked += rng.sample(group, min(k, len(group)))
    rng.shuffle(picked)
    return sorted(picked[:limit], key=lambda e: e.question_id)


def register_databases(cfg: Config, db_root: str | Path, db_ids: set[str]) -> None:
    root = Path(db_root).resolve()
    for db_id in db_ids:
        if db_id in cfg.databases:
            continue
        path = root / db_id / f"{db_id}.sqlite"
        if not path.exists():
            raise FileNotFoundError(f"missing database for {db_id}: {path}")
        desc = root / db_id / "database_description"
        cfg.databases[db_id] = DatabaseSpec(
            name=db_id, dialect="sqlite", path=str(path),
            description_dir=str(desc) if desc.is_dir() else None)
