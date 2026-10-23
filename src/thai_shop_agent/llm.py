"""Thin wrapper around the OpenAI-compatible chat API (course proxy).

Rate limits (429) and transient server/connection errors are retried here, not inside the
OpenAI client (max_retries=0), so the time spent waiting is counted in ``wait_seconds()`` and the
eval can report latency without it. Error text that leaves this module goes through ``redact``.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

import openai
from openai import OpenAI

from thai_shop_agent.config import EMBED_BATCH, EMBED_MODEL, MAX_TOKENS, MODEL, TEMPERATURE


MAX_ATTEMPTS = 4
MAX_WAIT_S = 65.0
_RESET = re.compile(r"resets at:?\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
_KEYISH = re.compile(r"(api[_ -]?key\W{0,3})\S+", re.IGNORECASE)
_waited = 0.0
T = TypeVar("T")


def redact(text: str) -> str:
    """Remove anything the proxy echoes after 'api_key' (it reports a key hash on 429s)."""
    return _KEYISH.sub(lambda m: m.group(1) + "[redacted]", text)


def wait_seconds() -> float:
    """Total seconds spent sleeping on rate limits / retries in this process."""
    return _waited


def _retry_delay(e: Exception, attempt: int) -> float:
    if isinstance(e, openai.RateLimitError):
        if m := _RESET.search(str(e)):
            reset = datetime.fromisoformat(m.group(1).replace(" ", "T")).replace(tzinfo=UTC)
            return min(MAX_WAIT_S, max(1.0, (reset - datetime.now(UTC)).total_seconds() + 1.0))
        return min(MAX_WAIT_S, 15.0 * attempt)
    return 2.0 * attempt


def with_retry(call: Callable[[], T]) -> T:
    global _waited
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return call()
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as e:
            if attempt == MAX_ATTEMPTS:
                raise
            delay = _retry_delay(e, attempt)
            time.sleep(delay)
            _waited += delay
    raise AssertionError("unreachable")


@dataclass
class Completion:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text as the model wrote it; parsed and validated by tools.py


@dataclass
class ToolCompletion(Completion):
    tool_calls: list[ToolCall]

    def assistant_message(self) -> dict:
        """The assistant turn to append to the history before the tool results."""
        msg: dict = {"role": "assistant", "content": self.text or None}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                                 for c in self.tool_calls]
        return msg


class LLM:
    def __init__(self, model: str = MODEL, temperature: float = TEMPERATURE, max_tokens: int = MAX_TOKENS):
        self.client = OpenAI(max_retries=0)  # OPENAI_API_KEY / OPENAI_BASE_URL from the environment
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def chat(self, messages: list[dict]) -> Completion:
        resp = with_retry(lambda: self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=self.temperature, max_tokens=self.max_tokens,
        ))
        usage = resp.usage
        return Completion(
            text=resp.choices[0].message.content or "",
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )

    def chat_tools(self, messages: list[dict], tools: list[dict]) -> ToolCompletion:
        """One call with native ``tools=``; the model may answer with text, tool calls, or both."""
        resp = with_retry(lambda: self.client.chat.completions.create(
            model=self.model, messages=messages, tools=tools, temperature=self.temperature,
            max_tokens=self.max_tokens,
        ))
        msg, usage = resp.choices[0].message, resp.usage
        calls = [ToolCall(id=c.id or f"call_{i}", name=c.function.name, arguments=c.function.arguments or "")
                 for i, c in enumerate(msg.tool_calls or [])]
        return ToolCompletion(
            text=msg.content or "",
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            tool_calls=calls,
        )


class Embedder:
    """Batch embeddings; vectors come back in input order (matched by ``data[i].index``)."""

    def __init__(self, model: str = EMBED_MODEL, batch_size: int = EMBED_BATCH, client: OpenAI | None = None):
        self.client = client or OpenAI(max_retries=0)
        self.model = model
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            resp = with_retry(lambda: self.client.embeddings.create(model=self.model, input=batch))
            data = sorted(resp.data, key=lambda d: d.index)
            if [d.index for d in data] != list(range(len(batch))):
                raise RuntimeError(f"embedding response has indexes {[d.index for d in data]} for {len(batch)} inputs")
            vectors += [list(d.embedding) for d in data]
        return vectors
