"""Optional column embeddings via the gateway's /v1/embeddings endpoint, cached on disk.

A schema has at most a few thousand columns, so a numpy matrix and a dot product
is all the "vector database" this needs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from .retriever import column_document


async def build_column_embeddings(schema, llm, model: str, cache_dir: Path,
                                  batch: int = 64) -> np.ndarray:
    docs = [column_document(t.name, c) for t in schema.tables.values() for c in t.columns]
    key = hashlib.sha256(("\n".join(docs) + model).encode()).hexdigest()[:16]
    path = Path(cache_dir) / f"{schema.name}-{key}.npy"
    if path.exists():
        return np.load(path)
    vecs: list[list[float]] = []
    for i in range(0, len(docs), batch):
        vecs.extend(await llm.embed(docs[i:i + batch], model))
    m = np.asarray(vecs, dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True).clip(min=1e-9)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, m)
    return m


def query_embedder(llm, model: str):
    async def embed(text: str) -> np.ndarray:
        return np.asarray((await llm.embed([text], model))[0], dtype=np.float32)
    return embed
