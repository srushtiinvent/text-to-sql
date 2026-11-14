"""HTTP API for the frontend.

    uvicorn api.main:app --reload --port 8000

Endpoints
  GET  /api/health
  GET  /api/databases                 configured databases
  GET  /api/databases/{name}/schema   tables, columns, foreign keys
  POST /api/ask                       full trace for one question
  POST /api/ask/stream                the same, as Server-Sent Events, one event per pipeline step
  GET  /api/runs                      eval runs found under runs/
  GET  /api/runs/{name}               summary + per-question records for one run
  GET  /api/runs/{name}/traces/{qid}  full trace of one eval question
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from t2s.config import load_config
from t2s.llm import GatewayLLM
from t2s.pipeline import Pipeline, public_trace

CONFIG_PATH = os.environ.get("T2S_CONFIG", "config.toml")
RUNS_DIR = Path(os.environ.get("T2S_RUNS_DIR", "runs"))
WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"

cfg = load_config(CONFIG_PATH)
state: dict = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    llm = GatewayLLM(cfg.gateway_url, cfg.gateway_key, cfg.gateway_timeout_s, cfg.gateway_max_retries)
    state["pipeline"] = Pipeline(cfg, llm)
    yield
    await llm.aclose()


app = FastAPI(title="Verified Text-to-SQL", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=cfg.cors_origins, allow_methods=["*"],
                   allow_headers=["*"])


# --------------------------------------------------------------- rate limit

_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # Behind Render/other proxies the client IP is the first X-Forwarded-For entry.
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def rate_limit(request: Request) -> None:
    """Sliding-window limit per IP. Every question costs several LLM calls on your gateway key."""
    limit = cfg.rate_limit_per_minute
    if limit <= 0:
        return
    now, q = time.monotonic(), _hits[_client_ip(request)]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        retry = int(60 - (now - q[0])) + 1
        raise HTTPException(429, f"rate limit: {limit} questions per minute",
                            headers={"Retry-After": str(retry)})
    q.append(now)


# ------------------------------------------------------------------- models

class AskRequest(BaseModel):
    database: str
    question: str = Field(min_length=3, max_length=500)
    evidence: str = Field(default="", max_length=500)


# --------------------------------------------------------------- endpoints

@app.get("/api/health")
async def health():
    return {"ok": True, "databases": list(cfg.databases)}


@app.get("/api/databases")
async def databases():
    out = []
    for name, spec in cfg.databases.items():
        available = (spec.dialect == "sqlite" and cfg.resolve(spec.path).exists()) or \
                    (spec.dialect == "postgres" and bool(os.environ.get(spec.dsn_env or "")))
        out.append({"name": name, "dialect": spec.dialect, "available": available})
    return out


@app.get("/api/databases/{name}/schema")
async def schema(name: str):
    try:
        ctx = await state["pipeline"].context(name)
    except KeyError:
        raise HTTPException(404, f"unknown database: {name}")
    return ctx.schema.to_dict()


@app.post("/api/ask")
async def ask(req: AskRequest, request: Request):
    rate_limit(request)
    _check_db(req.database)
    trace = await state["pipeline"].ask(req.database, req.question, req.evidence)
    return json.loads(json.dumps(public_trace(trace), default=str))


@app.post("/api/ask/stream")
async def ask_stream(req: AskRequest, request: Request):
    rate_limit(request)
    _check_db(req.database)
    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(kind: str, data: dict):
        await queue.put((kind, data))

    async def run():
        try:
            trace = await state["pipeline"].ask(req.database, req.question, req.evidence, on_event)
            await queue.put(("trace", public_trace(trace)))
        except Exception as e:
            await queue.put(("error", {"message": f"{type(e).__name__}: {e}"}))
        finally:
            await queue.put(None)

    async def events():
        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                kind, data = item
                yield f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"
        finally:
            if not task.done():  # client disconnected: stop spending LLM calls
                task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs")
async def runs():
    out = []
    if RUNS_DIR.is_dir():
        for d in sorted(RUNS_DIR.iterdir()):
            s = d / "summary.json"
            if s.exists():
                summary = json.loads(s.read_text())
                out.append({"name": d.name, "headline": summary["headline"],
                            "modified": s.stat().st_mtime})
    return sorted(out, key=lambda r: -r["modified"])


@app.get("/api/runs/{name}")
async def run_detail(name: str):
    d = _run_dir(name)
    summary = json.loads((d / "summary.json").read_text())
    records = [json.loads(l) for l in (d / "results.jsonl").read_text().splitlines() if l.strip()]
    ablation = (d / "ablation.md").read_text() if (d / "ablation.md").exists() else None
    return {"name": name, "summary": summary, "records": records, "ablation": ablation}


@app.get("/api/runs/{name}/traces/{qid}")
async def run_trace(name: str, qid: int):
    d = _run_dir(name)
    path = d / "traces.jsonl"
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                t = json.loads(line)
                if t.get("question_id") == qid:
                    return t
    raise HTTPException(404, "trace not found")


def _check_db(name: str):
    if name not in cfg.databases:
        raise HTTPException(404, f"unknown database: {name}")


def _run_dir(name: str) -> Path:
    d = (RUNS_DIR / name).resolve()
    if RUNS_DIR.resolve() not in d.parents or not (d / "summary.json").exists():
        raise HTTPException(404, "run not found")
    return d


# Serve the built frontend (web/dist) from the same origin in production.
if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        f = (WEB_DIST / path).resolve()
        inside = WEB_DIST.resolve() in f.parents
        return FileResponse(f if path and inside and f.is_file() else WEB_DIST / "index.html")
