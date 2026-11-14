"""Schema retrieval: choose the tables, columns and literal values a question needs.

Signals:
  * BM25 over a text document per column (names, split identifiers, descriptions, samples)
  * optional embeddings (through the gateway), fused with BM25 by reciprocal-rank fusion
  * value matches (a column containing a literal from the question is always relevant)
  * FK join-path completion (bridge tables between the chosen ones)

Small schemas skip retrieval entirely: below `full_schema_column_threshold`
columns, the whole schema fits in the prompt and pruning can only lose recall.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..schema import Schema, render_schema
from .bm25 import BM25
from .joins import connect_tables
from .text import split_identifier, tokenize
from .values import ValueIndex, ValueMatch


@dataclass
class RetrievalResult:
    mode: str  # "full" | "retrieved"
    tables: list[str]
    columns: dict[str, list[str]]
    table_scores: dict[str, float] = field(default_factory=dict)
    bridge_tables: list[str] = field(default_factory=list)
    value_matches: list[ValueMatch] = field(default_factory=list)
    schema_text: str = ""

    def hints_text(self) -> str:
        if not self.value_matches:
            return ""
        lines = [f"- {m.table}.{m.column} contains {m.value!r} (matched \"{m.span}\")"
                 for m in self.value_matches]
        return "Values found in the database that match words in the question:\n" + "\n".join(lines)

    def to_dict(self) -> dict:
        return {"mode": self.mode, "tables": self.tables, "columns": self.columns,
                "table_scores": {k: round(v, 4) for k, v in self.table_scores.items()},
                "bridge_tables": self.bridge_tables,
                "value_matches": [m.to_dict() for m in self.value_matches],
                "schema_text": self.schema_text}


class Retriever:
    def __init__(self, schema: Schema, value_index: ValueIndex | None, cfg,
                 column_embeddings: np.ndarray | None = None, embed_query=None):
        self.schema = schema
        self.values = value_index
        self.cfg = cfg
        self.col_keys: list[tuple[str, str]] = []
        docs = []
        for t in schema.tables.values():
            for c in t.columns:
                self.col_keys.append((t.name, c.name))
                docs.append(tokenize(column_document(t.name, c)))
        self.bm25 = BM25(docs)
        self.table_bm25_names = list(schema.tables)
        self.table_bm25 = BM25([tokenize(split_identifier(n)) for n in self.table_bm25_names])
        self.col_emb = column_embeddings
        self.embed_query = embed_query  # async fn(text) -> np.ndarray, optional

    async def retrieve(self, question: str, evidence: str = "") -> RetrievalResult:
        text = f"{question} {evidence}".strip()
        matches = self.values.match(text, self.cfg.value_match_threshold) if self.values else []

        if self.schema.column_count <= self.cfg.full_schema_column_threshold:
            tables = list(self.schema.tables)
            cols = {t: [c.name for c in self.schema.tables[t].columns] for t in tables}
            return RetrievalResult("full", tables, cols, value_matches=matches,
                                   schema_text=render_schema(self.schema, tables))

        q = tokenize(text)
        col_scores = np.array(self.bm25.scores(q), dtype=float)
        if self.col_emb is not None and self.embed_query is not None:
            qv = await self.embed_query(text)
            sims = self.col_emb @ (qv / (np.linalg.norm(qv) or 1.0))
            col_scores = rrf([col_scores, sims])

        table_scores: dict[str, float] = {}
        for (t, _c), s in zip(self.col_keys, col_scores):
            table_scores[t] = max(table_scores.get(t, 0.0), float(s))
        for t, s in zip(self.table_bm25_names, self.table_bm25.scores(q)):
            table_scores[t] = table_scores.get(t, 0.0) + float(s)
        for m in matches:  # a literal from the question lives here: strong evidence
            table_scores[m.table] = table_scores.get(m.table, 0.0) + 5.0 * m.score

        ranked = sorted(table_scores, key=lambda t: -table_scores[t])
        top = [t for t in ranked[: self.cfg.top_k_tables] if table_scores[t] > 0] or ranked[:2]
        for m in matches:
            if m.table not in top:
                top.append(m.table)
        tables, bridges = connect_tables(self.schema, top)

        columns = self._prune_columns(tables, dict(zip(self.col_keys, col_scores)), matches)
        return RetrievalResult(
            "retrieved", tables, columns,
            table_scores={t: table_scores.get(t, 0.0) for t in tables},
            bridge_tables=bridges, value_matches=matches,
            schema_text=render_schema(self.schema, tables, columns))

    def _prune_columns(self, tables, scores, matches) -> dict[str, list[str]]:
        out = {}
        limit = self.cfg.max_columns_per_table
        for t in tables:
            cols = [c.name for c in self.schema.tables[t].columns]
            if len(cols) <= limit:
                out[t] = cols
                continue
            keep = self.schema.key_columns(t) | {m.column for m in matches if m.table == t}
            by_score = sorted(cols, key=lambda c: -scores.get((t, c), 0.0))
            for c in by_score:
                if len(keep) >= limit:
                    break
                keep.add(c)
            out[t] = [c for c in cols if c in keep]  # keep original column order
        return out


def column_document(table: str, col) -> str:
    parts = [table, split_identifier(table), col.name, split_identifier(col.name),
             col.description, col.value_description, " ".join(col.samples)]
    return " ".join(p for p in parts if p)


def rrf(score_lists: list[np.ndarray], k: int = 60) -> np.ndarray:
    """Reciprocal-rank fusion: robust to the two scores being on different scales."""
    fused = np.zeros_like(score_lists[0], dtype=float)
    for scores in score_lists:
        ranks = np.empty(len(scores), dtype=float)
        ranks[np.argsort(-scores)] = np.arange(1, len(scores) + 1)
        fused += 1.0 / (k + ranks)
    return fused
