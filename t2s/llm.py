"""LLM client that talks to the LLM Gateway (OpenAI-compatible) plus a fake for tests.

All model traffic goes through the gateway, so caching, provider fallback and
cost logging happen there. This client only adds usage accounting and retries
for transport-level failures.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    by_tag: dict[str, int] = field(default_factory=dict)

    def add(self, r: LLMResponse, tag: str) -> None:
        self.calls += 1
        self.prompt_tokens += r.prompt_tokens
        self.completion_tokens += r.completion_tokens
        self.latency_ms += r.latency_ms
        self.by_tag[tag] = self.by_tag.get(tag, 0) + 1

    def to_dict(self) -> dict:
        return dict(calls=self.calls, prompt_tokens=self.prompt_tokens,
                    completion_tokens=self.completion_tokens,
                    latency_ms=round(self.latency_ms, 1), by_tag=dict(self.by_tag))


class LLM(Protocol):
    async def chat(self, messages: list[dict], model: str, temperature: float = 0.0,
                   max_tokens: int = 1024, tag: str = "") -> LLMResponse: ...

    async def embed(self, texts: list[str], model: str) -> list[list[float]]: ...


class GatewayLLM:
    """OpenAI-format client for the LLM Gateway."""

    def __init__(self, base_url: str, api_key: str, timeout_s: float = 60.0, max_retries: int = 2):
        import httpx  # imported lazily so the core package works without it in tests

        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout_s,
        )
        self._httpx = httpx
        self.max_retries = max_retries

    async def chat(self, messages, model, temperature=0.0, max_tokens=1024, tag=""):
        body = {"model": model, "messages": messages, "temperature": temperature,
                "max_tokens": max_tokens}
        data = await self._post("/v1/chat/completions", body)
        usage = data.get("usage") or {}
        return LLMResponse(
            text=data["choices"][0]["message"].get("content") or "",
            model=data.get("model", model),
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=data.pop("_latency_ms", 0.0),
        )

    async def embed(self, texts, model):
        data = await self._post("/v1/embeddings", {"model": model, "input": texts})
        return [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]

    async def _post(self, path: str, body: dict) -> dict:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            start = time.perf_counter()
            try:
                resp = await self._client.post(path, json=body)
                if resp.status_code in (429, 502, 503, 504) and attempt < self.max_retries:
                    retry_after = float(resp.headers.get("retry-after", 2 ** attempt))
                    await asyncio.sleep(min(retry_after, 30))
                    continue
                resp.raise_for_status()
                data = resp.json()
                data["_latency_ms"] = (time.perf_counter() - start) * 1000
                return data
            except (self._httpx.TransportError, self._httpx.TimeoutException) as e:
                last_exc = e
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError(f"gateway request failed after retries: {last_exc}")

    async def aclose(self) -> None:
        await self._client.aclose()


Responder = Callable[[list[dict], str, float, str], str]


class FakeLLM:
    """Deterministic LLM for tests: a function maps (messages, model, temperature, tag) -> text."""

    def __init__(self, responder: Responder):
        self.responder = responder
        self.calls: list[dict] = []

    async def chat(self, messages, model, temperature=0.0, max_tokens=1024, tag=""):
        self.calls.append({"messages": messages, "model": model,
                           "temperature": temperature, "tag": tag})
        text = self.responder(messages, model, temperature, tag)
        return LLMResponse(text=text, model=model, prompt_tokens=len(str(messages)) // 4,
                           completion_tokens=len(text) // 4)

    async def embed(self, texts, model):
        raise NotImplementedError


class TrackedLLM:
    """Wraps any LLM and records usage per pipeline run."""

    def __init__(self, inner: LLM):
        self.inner = inner
        self.usage = Usage()

    async def chat(self, messages, model, temperature=0.0, max_tokens=1024, tag=""):
        r = await self.inner.chat(messages, model, temperature, max_tokens, tag)
        self.usage.add(r, tag)
        return r

    async def embed(self, texts, model):
        return await self.inner.embed(texts, model)


# ------------------------------------------------------------------ parsing

_FENCE = re.compile(r"```(?:sql|sqlite|postgresql|postgres)?\s*\n(.*?)```", re.S | re.I)
_SQL_START = re.compile(r"\b(WITH|SELECT)\b", re.I)


def extract_sql(text: str) -> str:
    """Pull the SQL out of a model response (last fenced block wins; else from the first SELECT/WITH)."""
    blocks = _FENCE.findall(text)
    if blocks:
        sql = blocks[-1]
    else:
        m = _SQL_START.search(text)
        sql = text[m.start():] if m else text
    sql = sql.strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    return sql


def extract_json(text: str) -> dict:
    """Parse the first JSON object in a model response; tolerate code fences and chatter."""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return {}
