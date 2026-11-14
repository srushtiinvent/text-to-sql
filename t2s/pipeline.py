"""The full question -> verified answer pipeline.

    retrieve -> generate N candidates (with self-repair) -> cluster by result
      -> [disagreement] find a distinguishing example -> judge picks
      -> [agreement]    judge verifies the single answer
      -> confidence score -> answer or abstain with a clarifying question

Every step is recorded in a trace dict (shown by the frontend, saved by the eval
runner) and optionally streamed to `on_event` as it happens.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import confidence as conf
from .config import Config
from .execute import make_executor
from .execute.base import jsonable
from .generate import generate_candidates
from .llm import TrackedLLM
from .retrieval import Retriever
from .retrieval.embeddings import build_column_embeddings, query_embedder
from .retrieval.values import ValueIndex
from .schema import introspect_postgres, introspect_sqlite, load_descriptions, quote_ident
from .verify.cluster import cluster_candidates
from .verify.distinguish import (PostgresRowSource, SQLiteRowSource,
                                 find_distinguishing_example)
from .verify.judge import clarifying_question, judge_pick, judge_verify

EventFn = Callable[[str, dict], Awaitable[None]]


@dataclass
class DatabaseContext:
    name: str
    dialect: str
    schema: object
    executor: object
    retriever: Retriever
    row_source: object


class Pipeline:
    def __init__(self, cfg: Config, llm):
        self.cfg = cfg
        self.llm = llm
        self._contexts: dict[str, DatabaseContext] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.weights = conf.load_weights(cfg.resolve(cfg.confidence_weights_path))

    # ----------------------------------------------------------- db contexts

    async def context(self, name: str) -> DatabaseContext:
        if name in self._contexts:
            return self._contexts[name]
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            if name not in self._contexts:
                self._contexts[name] = await self._build_context(name)
        return self._contexts[name]

    async def _build_context(self, name: str) -> DatabaseContext:
        spec = self.cfg.databases.get(name)
        if spec is None:
            raise KeyError(f"unknown database: {name}")
        executor = make_executor(spec, self.cfg)
        if spec.dialect == "sqlite":
            schema = await asyncio.to_thread(introspect_sqlite, self.cfg.resolve(spec.path), name)
            row_source = SQLiteRowSource(executor)
        else:
            schema = await asyncio.to_thread(introspect_postgres, executor.dsn, name)
            row_source = PostgresRowSource(executor)
        if spec.description_dir:
            load_descriptions(schema, self.cfg.resolve(spec.description_dir))

        def fetch_distinct(table, column, limit):
            sql = (f"SELECT DISTINCT {quote_ident(column)} FROM {quote_ident(table)} "
                   f"WHERE {quote_ident(column)} IS NOT NULL LIMIT {int(limit)}")
            return [r[0] for r in executor.run(sql).rows]

        values = await asyncio.to_thread(
            ValueIndex.build, fetch_distinct, schema,
            self.cfg.value_index_max_values_per_column, self.cfg.value_index_max_value_length)
        col_emb = embed_q = None
        if self.cfg.use_embeddings and self.cfg.embedding_model:
            col_emb = await build_column_embeddings(schema, self.llm, self.cfg.embedding_model,
                                                    self.cfg.resolve("data/cache/embeddings"))
            embed_q = query_embedder(self.llm, self.cfg.embedding_model)
        retriever = Retriever(schema, values, self.cfg, col_emb, embed_q)
        return DatabaseContext(name, spec.dialect, schema, executor, retriever, row_source)

    # --------------------------------------------------------------- the run

    async def ask(self, database: str, question: str, evidence: str = "",
                  on_event: EventFn | None = None) -> dict:
        cfg = self.cfg
        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        llm = TrackedLLM(self.llm)

        async def emit(kind: str, data: dict):
            if on_event:
                await on_event(kind, data)

        def lap(name: str, start: float):
            timings[name] = round((time.perf_counter() - start) * 1000, 1)

        ctx = await self.context(database)
        trace: dict = {"id": uuid.uuid4().hex[:12], "database": database, "question": question,
                       "evidence": evidence, "dialect": ctx.dialect}

        # 1. retrieval
        s = time.perf_counter()
        retrieval = await ctx.retriever.retrieve(question, evidence)
        lap("retrieval", s)
        trace["retrieval"] = retrieval.to_dict()
        await emit("retrieval", trace["retrieval"])

        # 2. candidates
        s = time.perf_counter()

        async def on_candidate(c):
            await emit("candidate", c.to_dict(cfg.preview_rows))

        cands = await generate_candidates(llm, cfg, ctx.executor, question=question,
                                          evidence=evidence, retrieval=retrieval,
                                          dialect=ctx.dialect, on_candidate=on_candidate)
        lap("generation", s)

        # 3. clustering
        clusters = cluster_candidates(cands)
        by_id = {c.id: c for c in cands}
        trace["candidates"] = [c.to_dict(cfg.preview_rows) for c in cands]
        trace["clusters"] = [dict(cl.to_dict(), representative_label=by_id[cl.representative].label)
                             for cl in clusters]
        await emit("clusters", {"clusters": trace["clusters"]})

        example = None
        decision = None
        chosen: int | None = clusters[0].id if clusters else None

        # 4. verification
        s = time.perf_counter()
        if len(clusters) >= 2:
            a, b = by_id[clusters[0].representative], by_id[clusters[1].representative]
            sqls = {a.label: a.sql, b.label: b.sql}
            if cfg.distinguish:
                ds = time.perf_counter()
                example = await asyncio.to_thread(
                    find_distinguishing_example, ctx.row_source, ctx.schema, sqls,
                    sample_sizes=cfg.sample_sizes, budget=cfg.ddmin_budget,
                    seed=cfg.distinguish_seed, timeout_s=min(cfg.timeout_s, 5.0))
                lap("distinguish", ds)
                trace["distinguishing"] = example.to_dict() if example else None
                await emit("distinguishing", {"example": trace["distinguishing"]})
            if cfg.judge:
                previews = {c.label: _preview_text(c, 10) for c in (a, b)}
                decision = await judge_pick(
                    llm, cfg.judge_model, question=question, evidence=evidence,
                    schema_text=retrieval.schema_text, options=sqls,
                    example_text=example.render() if example else None, previews=previews)
                if decision.choice is not None:
                    chosen = next(c for c in (a, b) if c.label == decision.choice).cluster
        elif len(clusters) == 1 and cfg.judge and cfg.judge_verify_single:
            rep = by_id[clusters[0].representative]
            decision = await judge_verify(
                llm, cfg.judge_model, question=question, evidence=evidence,
                schema_text=retrieval.schema_text, label=rep.label, sql=rep.sql,
                preview=_preview_text(rep, 10), row_count=len(rep.result.rows))
        lap("verification", s)
        trace["judge"] = decision.to_dict() if decision else None
        if decision:
            await emit("judge", trace["judge"])

        # 5. confidence
        feats = conf.features(len(cands), clusters, chosen, decision)
        score = conf.score(feats, self.weights)
        abstained = chosen is None or score < cfg.confidence_threshold
        trace["confidence"] = {"score": round(score, 4), "features": feats,
                               "threshold": cfg.confidence_threshold, "abstained": abstained}

        # 6. answer or clarifying question
        clarify = None
        if abstained and cfg.judge and chosen is not None:
            difference = (example.render() if example else
                          "Possible reading:\n" + _preview_text(by_id[clusters[chosen].representative], 5))
            try:
                clarify = await clarifying_question(llm, cfg.clarify_model,
                                                    question=question, difference=difference)
            except Exception:
                clarify = None
        trace["clarifying_question"] = clarify
        if chosen is not None:
            rep = by_id[clusters[chosen].representative]
            trace["answer"] = {"candidate": rep.label, "sql": rep.sql, "cluster": chosen,
                               **rep.result.preview(cfg.preview_rows)}
        else:
            trace["answer"] = None

        timings["total"] = round((time.perf_counter() - t0) * 1000, 1)
        trace["timings_ms"] = timings
        trace["usage"] = llm.usage.to_dict()
        # Full in-memory objects for the eval runner (stripped before JSON responses).
        trace["_internal"] = {"candidates": cands, "clusters": clusters, "chosen": chosen,
                              "decision": decision, "schema": ctx.schema,
                              "executor": ctx.executor}
        await emit("final", {"answer": trace["answer"], "confidence": trace["confidence"],
                             "clarifying_question": clarify, "usage": trace["usage"],
                             "timings_ms": timings})
        return trace


def _preview_text(cand, n: int) -> str:
    r = cand.result
    if r is None:
        return f"(error: {cand.error})"
    head = " | ".join(r.columns)
    lines = [" | ".join("NULL" if v is None else str(jsonable(v)) for v in row) for row in r.rows[:n]]
    more = f"\n... ({len(r.rows)} rows total)" if len(r.rows) > n else ""
    return head + "\n" + ("\n".join(lines) if lines else "(no rows)") + more


def public_trace(trace: dict) -> dict:
    """Drop in-memory objects so the trace can be serialised."""
    return {k: v for k, v in trace.items() if not k.startswith("_")}
