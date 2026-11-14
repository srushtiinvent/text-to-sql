"""Configuration loaded from a TOML file (stdlib tomllib, no extra dependency).

Every component reads its settings from here, so ablations are just config overrides.
"""
from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CandidateSpec:
    model: str
    prompt: str = "direct"  # direct | plan | decompose
    temperature: float = 0.0


@dataclass
class DatabaseSpec:
    name: str
    dialect: str  # sqlite | postgres
    path: str | None = None  # sqlite file
    dsn_env: str | None = None  # env var holding a Postgres DSN (read-only user)
    description_dir: str | None = None  # BIRD-style database_description/ folder


@dataclass
class Config:
    # gateway
    gateway_url: str = "http://localhost:8080"
    gateway_key_env: str = "GATEWAY_KEY"
    gateway_timeout_s: float = 60.0
    gateway_max_retries: int = 2

    # models
    judge_model: str = "auto"
    clarify_model: str = "auto"
    embedding_model: str = ""

    # generation
    candidates: list[CandidateSpec] = field(default_factory=lambda: [CandidateSpec("auto")])
    max_repairs: int = 2
    repair_empty_result: bool = False
    max_tokens: int = 1024

    # retrieval
    full_schema_column_threshold: int = 60
    top_k_tables: int = 6
    max_columns_per_table: int = 40
    value_index_max_values_per_column: int = 5000
    value_index_max_value_length: int = 80
    value_match_threshold: float = 0.75
    use_embeddings: bool = False

    # execution
    timeout_s: float = 10.0
    row_limit: int = 10000
    preview_rows: int = 20

    # verification
    distinguish: bool = True
    judge: bool = True
    judge_verify_single: bool = True
    sample_sizes: list[int] = field(default_factory=lambda: [8, 32, 128, 512, 2048])
    ddmin_budget: int = 250
    distinguish_seed: int = 7

    # confidence
    confidence_threshold: float = 0.5
    confidence_weights_path: str = "data/confidence_weights.json"

    # eval
    eval_concurrency: int = 4

    # api
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5173"])
    rate_limit_per_minute: int = 10

    databases: dict[str, DatabaseSpec] = field(default_factory=dict)
    base_dir: Path = field(default_factory=Path.cwd)

    @property
    def gateway_key(self) -> str:
        return os.environ.get(self.gateway_key_env, "")

    def resolve(self, p: str | None) -> Path | None:
        if p is None:
            return None
        path = Path(p)
        return path if path.is_absolute() else (self.base_dir / path)

    def with_overrides(self, **overrides: Any) -> "Config":
        new = copy.deepcopy(self)
        for k, v in overrides.items():
            if not hasattr(new, k):
                raise KeyError(f"unknown config key: {k}")
            if k == "candidates":  # accept JSON from the command line
                v = [c if isinstance(c, CandidateSpec) else CandidateSpec(**c) for c in v]
            setattr(new, k, v)
        return new


_SECTION_KEYS = {
    "gateway": {"url": "gateway_url", "api_key_env": "gateway_key_env",
                "timeout_s": "gateway_timeout_s", "max_retries": "gateway_max_retries"},
    "models": {"judge": "judge_model", "clarify": "clarify_model", "embeddings": "embedding_model"},
    "generation": {"max_repairs": "max_repairs", "repair_empty_result": "repair_empty_result",
                   "max_tokens": "max_tokens"},
    "retrieval": {"full_schema_column_threshold": "full_schema_column_threshold",
                  "top_k_tables": "top_k_tables", "max_columns_per_table": "max_columns_per_table",
                  "value_index_max_values_per_column": "value_index_max_values_per_column",
                  "value_index_max_value_length": "value_index_max_value_length",
                  "value_match_threshold": "value_match_threshold",
                  "use_embeddings": "use_embeddings"},
    "execution": {"timeout_s": "timeout_s", "row_limit": "row_limit", "preview_rows": "preview_rows"},
    "verify": {"distinguish": "distinguish", "judge": "judge",
               "judge_verify_single": "judge_verify_single", "sample_sizes": "sample_sizes",
               "ddmin_budget": "ddmin_budget", "seed": "distinguish_seed"},
    "confidence": {"threshold": "confidence_threshold", "weights_path": "confidence_weights_path"},
    "eval": {"concurrency": "eval_concurrency"},
    "api": {"cors_origins": "cors_origins", "rate_limit_per_minute": "rate_limit_per_minute"},
}


def load_config(path: str | Path) -> Config:
    path = Path(path)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    cfg = Config(base_dir=path.resolve().parent)
    for section, mapping in _SECTION_KEYS.items():
        for key, attr in mapping.items():
            if key in raw.get(section, {}):
                setattr(cfg, attr, raw[section][key])
    cands = raw.get("generation", {}).get("candidates")
    if cands:
        cfg.candidates = [CandidateSpec(**c) for c in cands]
    for name, spec in raw.get("databases", {}).items():
        cfg.databases[name] = DatabaseSpec(name=name, **spec)
    return cfg
