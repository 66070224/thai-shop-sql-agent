"""Embedding retrieval over the schema docs and the few-shot bank.

Chunks:
  - one per ``##`` section of schema_docs.md; kind = table / rule / relationships (``table`` set for table chunks)
  - one per example in examples.jsonl; kind = example, only the question text is embedded (``sql`` kept as metadata)

The index (data/index.json) stores every chunk with its bge-m3 vector plus the sha256 of the
sources, so a stale index is detected instead of silently used. Similarity is plain-Python cosine.

Prompt context (default): the relationships chunk always; tables and rules ranked together from
the same index; DDL for the tables whose chunk was retrieved plus the tables a retrieved rule names
(rule chunks carry those in ``tables``; chosen from eval/results/retrieval_20261023-111756.json:
recall@3 0.742 -> 0.956). B2 and the agent add the top-k examples as question -> SQL pairs.
"""

import hashlib
import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from thai_shop_agent.config import DEFAULT_RETRIEVAL, EMBED_MODEL, EXAMPLES, INDEX_PATH, SCHEMA_DOCS, TABLES

SCHEMA_KINDS = ("table", "rule")  # what search_schema ranks; relationships is always included
INDEX_VERSION = 2  # bump when chunking or chunk metadata changes, so an old index is rebuilt


class EmbedderLike(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class Chunk:
    id: str
    kind: str  # table | rule | relationships | example
    text: str
    table: str | None = None
    sql: str | None = None
    tables: tuple[str, ...] = field(default_factory=tuple)  # rule: tables it names; example: tables its SQL uses


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


class StaleIndexError(RuntimeError):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def tables_named(text: str) -> tuple[str, ...]:
    """Tables mentioned by name in a text, in config.TABLES order."""
    return tuple(t for t in TABLES if re.search(rf"\b{t}\b", text))


def schema_chunks(markdown: str) -> list[Chunk]:
    """Split schema_docs.md at ``## `` headings; text before the first heading is not a chunk."""
    chunks = []
    for section in re.split(r"(?m)^(?=## )", markdown)[1:]:
        heading = section.splitlines()[0][3:].strip()
        text = section.strip()
        if m := re.match(r"Table:\s*(\w+)", heading):
            chunks.append(Chunk(id=f"table:{m.group(1)}", kind="table", text=text, table=m.group(1)))
        elif heading.startswith("Relationships"):
            chunks.append(Chunk(id="relationships", kind="relationships", text=text))
        elif m := re.match(r"Rule:\s*([^(]+)", heading):
            chunks.append(Chunk(id=f"rule:{_slug(m.group(1))}", kind="rule", text=text, tables=tables_named(text)))
        else:
            raise ValueError(f"schema_docs.md heading {heading!r} is not Table:, Rule: or Relationships")
    return chunks


def example_chunks(examples: Iterable[dict]) -> list[Chunk]:
    return [Chunk(id=e["id"], kind="example", text=e["question"], sql=e["sql"], tables=tuple(e["tables"]))
            for e in examples]


def load_examples(path: Path = EXAMPLES) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_chunks(docs: Path = SCHEMA_DOCS, examples: Path = EXAMPLES) -> list[Chunk]:
    return schema_chunks(docs.read_text(encoding="utf-8")) + example_chunks(load_examples(examples))


def source_hashes(docs: Path = SCHEMA_DOCS, examples: Path = EXAMPLES) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (docs, examples)}


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def build_index(embedder: EmbedderLike, chunks: list[Chunk], sources: dict[str, str],
                path: Path = INDEX_PATH, model: str = EMBED_MODEL) -> dict:
    """Embed every chunk in one batched pass (vector i belongs to chunk i) and write the index."""
    vectors = embedder.embed([c.text for c in chunks])
    if len(vectors) != len(chunks):
        raise RuntimeError(f"got {len(vectors)} vectors for {len(chunks)} chunks")
    index = {
        "version": INDEX_VERSION,
        "model": model,
        "dim": len(vectors[0]) if vectors else 0,
        "sources": sources,
        "chunks": [{**asdict(c), "embedding": v} for c, v in zip(chunks, vectors)],
    }
    path.write_text(json.dumps(index, ensure_ascii=False) + "\n", encoding="utf-8")
    return index


def _matches(chunk: Chunk, kind: str | Sequence[str] | None) -> bool:
    if kind is None:
        return True
    return chunk.kind == kind if isinstance(kind, str) else chunk.kind in kind


class Retriever:
    def __init__(self, embedder: EmbedderLike, path: Path = INDEX_PATH,
                 expected_sources: dict[str, str] | None = None):
        if not path.exists():
            raise FileNotFoundError(f"{path} not found; run: uv run --env-file .env scripts/build_index.py")
        index = json.loads(path.read_text(encoding="utf-8"))
        expected = source_hashes() if expected_sources is None else expected_sources
        if index.get("version") != INDEX_VERSION or index["sources"] != expected:
            raise StaleIndexError(f"{path.name} was built from different schema docs / examples; "
                                  "rebuild it with scripts/build_index.py")
        self.embedder = embedder
        self.model = index["model"]
        self.chunks: list[Chunk] = []
        self.vectors: list[list[float]] = []
        for row in index["chunks"]:
            vector = row.pop("embedding")
            row["tables"] = tuple(row["tables"])
            self.chunks.append(Chunk(**row))
            self.vectors.append(vector)

    def by_kind(self, kind: str) -> list[Chunk]:
        return [c for c in self.chunks if c.kind == kind]

    def embed_query(self, query: str) -> list[float]:
        return self.embedder.embed([query])[0]

    def rank(self, vector: Sequence[float], kind: str | Sequence[str] | None = None) -> list[Hit]:
        """Every chunk of the given kind(s), best first (ties keep index order)."""
        hits = [Hit(c, cosine(vector, v)) for c, v in zip(self.chunks, self.vectors) if _matches(c, kind)]
        return sorted(hits, key=lambda h: -h.score)

    def search_vector(self, vector: Sequence[float], k: int, min_score: float,
                      kind: str | Sequence[str] | None = None) -> list[Hit]:
        return [h for h in self.rank(vector, kind) if h.score >= min_score][:k]

    def search(self, query: str, k: int = DEFAULT_RETRIEVAL.k_schema, min_score: float = DEFAULT_RETRIEVAL.min_score,
               kind: str | Sequence[str] | None = None) -> list[Hit]:
        """Top-k chunks with cosine score >= min_score (defaults from the sweep, see config.DEFAULT_RETRIEVAL)."""
        return self.search_vector(self.embed_query(query), k, min_score, kind)


@dataclass
class SchemaContext:
    text: str
    hits: list[Hit]  # retrieved table / rule chunks
    tables: list[str]  # tables whose DDL was included
    chunk_ids: list[str]  # every schema chunk placed in the prompt (for citations)


def build_schema_context(retriever: Retriever, vector: Sequence[float], k: int, min_score: float,
                         ddl: dict[str, str], rule_tables: bool = True) -> SchemaContext:
    """Relationships + top-k table/rule chunks + DDL of the retrieved tables (and, with
    ``rule_tables``, of the tables the retrieved rules name)."""
    hits = retriever.search_vector(vector, k, min_score, kind=SCHEMA_KINDS)
    tables = [h.chunk.table for h in hits if h.chunk.kind == "table"]
    if rule_tables:
        tables += [t for h in hits if h.chunk.kind == "rule" for t in h.chunk.tables]
    tables = list(dict.fromkeys(tables))  # dedupe, keep rank order
    relationships = retriever.by_kind("relationships")
    ddl_text = "\n\n".join(ddl[t] for t in tables if t in ddl) or "(no table retrieved)"
    docs = "\n\n".join([c.text for c in relationships] + [h.chunk.text for h in hits])
    text = (f"## Database schema (SQLite DDL of the relevant tables)\n\n{ddl_text}\n\n"
            f"## Schema documentation and business rules\n\n{docs}")
    return SchemaContext(text=text, hits=hits, tables=tables,
                         chunk_ids=[c.id for c in relationships] + [h.chunk.id for h in hits])


@dataclass
class PromptContext:
    text: str  # schema section, then the examples section when there are examples
    schema: SchemaContext
    examples: list[Hit]

    @property
    def chunk_ids(self) -> list[str]:
        return self.schema.chunk_ids + [h.chunk.id for h in self.examples]


def format_examples(hits: list[Hit]) -> str:
    pairs = "\n\n".join(f"Question: {h.chunk.text}\nSQL: {h.chunk.sql}" for h in hits)
    return f"## Similar questions with their correct SQL\n\n{pairs}"


def build_prompt_context(retriever: Retriever, question: str, k_schema: int, k_examples: int, min_score: float,
                         ddl: dict[str, str], rule_tables: bool = True) -> PromptContext:
    """The context B1 (k_examples = 0), B2 and the agent start from; the question is embedded once."""
    vector = retriever.embed_query(question)
    schema = build_schema_context(retriever, vector, k_schema, min_score, ddl, rule_tables)
    examples = retriever.search_vector(vector, k_examples, min_score, kind="example") if k_examples else []
    text = schema.text + (f"\n\n{format_examples(examples)}" if examples else "")
    return PromptContext(text=text, schema=schema, examples=examples)
