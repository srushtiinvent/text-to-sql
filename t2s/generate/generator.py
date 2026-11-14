"""Generate several SQL candidates in parallel, each with its own self-correction loop."""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field

from ..execute.base import ExecutionError, ResultSet
from ..llm import extract_sql
from .prompts import REPAIR, REPAIR_EMPTY, build_messages


@dataclass
class Attempt:
    sql: str
    error: str | None = None
    error_kind: str | None = None


@dataclass
class Candidate:
    id: int
    label: str
    model: str
    prompt: str
    temperature: float
    sql: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    result: ResultSet | None = None
    error: str | None = None
    error_kind: str | None = None
    cluster: int | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and self.error is None

    def to_dict(self, preview_rows: int) -> dict:
        return {
            "id": self.id, "label": self.label, "model": self.model, "prompt": self.prompt,
            "temperature": self.temperature, "sql": self.sql,
            "attempts": [a.__dict__ for a in self.attempts],
            "result": self.result.preview(preview_rows) if self.result else None,
            "error": self.error, "error_kind": self.error_kind, "cluster": self.cluster,
        }


async def generate_candidates(llm, cfg, executor, *, question: str, evidence: str,
                              retrieval, dialect: str, on_candidate=None) -> list[Candidate]:
    specs = cfg.candidates
    # If two candidates share (model, prompt), a gateway cache keyed on the conversation
    # could return the same text twice. A per-sample note keeps the requests distinct.
    dupes = Counter((s.model, s.prompt) for s in specs)
    seen: Counter = Counter()

    async def one(i: int, spec) -> Candidate:
        key = (spec.model, spec.prompt)
        seen[key] += 1
        note = f"(Independent sample #{seen[key]}.)" if dupes[key] > 1 else ""
        cand = Candidate(id=i, label=_label(i), model=spec.model, prompt=spec.prompt,
                         temperature=spec.temperature)
        messages = build_messages(spec.prompt, dialect=dialect, schema_text=retrieval.schema_text,
                                  question=question, evidence=evidence,
                                  hints=retrieval.hints_text(), variant_note=note)
        await _run_with_repairs(llm, cfg, executor, cand, messages, retrieval)
        if on_candidate:
            await on_candidate(cand)
        return cand

    return list(await asyncio.gather(*(one(i, s) for i, s in enumerate(specs))))


async def _run_with_repairs(llm, cfg, executor, cand: Candidate, messages: list[dict], retrieval):
    empty_repairs_left = 1 if cfg.repair_empty_result else 0
    for attempt_no in range(cfg.max_repairs + 1):
        try:
            resp = await llm.chat(messages, cand.model, cand.temperature, cfg.max_tokens,
                                  tag="generate" if attempt_no == 0 else "repair")
        except Exception as e:  # gateway down after its own fallbacks: record and stop
            cand.error, cand.error_kind = f"LLM call failed: {e}", "llm"
            return
        sql = extract_sql(resp.text)
        cand.sql = sql
        try:
            result = await asyncio.to_thread(executor.run, sql)
        except ExecutionError as e:
            cand.attempts.append(Attempt(sql, str(e), e.kind))
            cand.error, cand.error_kind, cand.result = str(e), e.kind, None
            if e.kind == "guard":  # never "repair" a write attempt into something else silently
                return
            messages = messages + [{"role": "assistant", "content": resp.text},
                                   {"role": "user", "content": REPAIR.format(sql=sql, error=e)}]
            continue
        cand.attempts.append(Attempt(sql))
        cand.result, cand.error, cand.error_kind = result, None, None
        if not result.rows and empty_repairs_left and attempt_no < cfg.max_repairs:
            empty_repairs_left -= 1
            hints = retrieval.hints_text()
            messages = messages + [{"role": "assistant", "content": resp.text},
                                   {"role": "user", "content": REPAIR_EMPTY.format(
                                       sql=sql, hints=(hints + "\n\n") if hints else "")}]
            continue
        return


def _label(i: int) -> str:
    return chr(ord("A") + i) if i < 26 else f"C{i}"
