"""Thin wrapper around the OpenAI-compatible chat API (course proxy)."""

from dataclasses import dataclass

from openai import OpenAI

from thai_shop_agent.config import EMBED_BATCH, EMBED_MODEL, MAX_TOKENS, MODEL, TEMPERATURE


@dataclass
class Completion:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None


class LLM:
    def __init__(self, model: str = MODEL, temperature: float = TEMPERATURE, max_tokens: int = MAX_TOKENS):
        self.client = OpenAI()  # reads OPENAI_API_KEY and OPENAI_BASE_URL from the environment
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def chat(self, messages: list[dict]) -> Completion:
        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=self.temperature, max_tokens=self.max_tokens,
        )
        usage = resp.usage
        return Completion(
            text=resp.choices[0].message.content or "",
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )


class Embedder:
    """Batch embeddings; vectors come back in input order (matched by ``data[i].index``)."""

    def __init__(self, model: str = EMBED_MODEL, batch_size: int = EMBED_BATCH, client: OpenAI | None = None):
        self.client = client or OpenAI()
        self.model = model
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            resp = self.client.embeddings.create(model=self.model, input=batch)
            data = sorted(resp.data, key=lambda d: d.index)
            if [d.index for d in data] != list(range(len(batch))):
                raise RuntimeError(f"embedding response has indexes {[d.index for d in data]} for {len(batch)} inputs")
            vectors += [list(d.embedding) for d in data]
        return vectors
