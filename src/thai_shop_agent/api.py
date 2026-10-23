"""HTTP API: the same Agent the eval runs, streamed as Server-Sent Events.

    uv run --env-file .env uvicorn thai_shop_agent.api:app --reload

POST /ask     {"question": "..."} (1-500 chars) -> text/event-stream, one ``data: {json}\\n\\n`` frame per
              agent event (retrieval, tool_call, tool_result, answer | error); every frame has ``type``
GET  /health  liveness + whether the database and the index exist
GET  /        web/index.html
An LLM / proxy / retrieval failure becomes an ``error`` frame and the stream ends; the server keeps
running. Error text is redacted and request bodies / keys are never logged.
"""

import json
import logging
from collections.abc import Callable, Iterator
from functools import lru_cache
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from thai_shop_agent.agent import Agent
from thai_shop_agent.config import DEFAULT_RETRIEVAL, INDEX_PATH, ROOT
from thai_shop_agent.db import DB_PATH, connect_ro
from thai_shop_agent.llm import LLM, Embedder, redact
from thai_shop_agent.retrieval import Retriever

WEB_INDEX = ROOT / "web" / "index.html"
log = logging.getLogger("thai_shop_agent.api")

Runner = Callable[[str], Iterator[dict[str, Any]]]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@lru_cache(maxsize=1)
def _shared() -> tuple[LLM, Retriever]:
    """Clients and the index are loaded once and shared; they are read-only."""
    return LLM(), Retriever(Embedder())


def agent_runner(question: str) -> Iterator[dict[str, Any]]:
    """One agent per request with its own read-only connection (closed when the stream ends)."""
    llm, retriever = _shared()
    conn = connect_ro(check_same_thread=False)
    try:
        yield from Agent(llm, retriever, conn, DEFAULT_RETRIEVAL).run(question)
    finally:
        conn.close()


def get_runner() -> Runner:
    return agent_runner


def frame(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def event_stream(runner: Runner, question: str) -> Iterator[str]:
    try:
        for event in runner(question):
            yield frame(event)
            if event.get("type") == "error":
                return
    except Exception as e:  # noqa: BLE001 - setup failures (missing index, proxy down) end the stream cleanly
        message = redact(f"{type(e).__name__}: {e}")
        log.warning("ask failed: %s", message)
        yield frame({"type": "error", "message": message})


app = FastAPI(title="Thai Shop SQL Agent")


@app.post("/ask")
def ask(req: AskRequest, runner: Runner = Depends(get_runner)) -> StreamingResponse:
    return StreamingResponse(event_stream(runner, req.question), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "database": DB_PATH.exists(), "index": INDEX_PATH.exists()}


@app.get("/", response_model=None)
def index() -> FileResponse | JSONResponse:
    if not WEB_INDEX.exists():
        return JSONResponse({"detail": "web/index.html not found"}, status_code=404)
    return FileResponse(WEB_INDEX, media_type="text/html; charset=utf-8")
