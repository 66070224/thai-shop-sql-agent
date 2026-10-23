"""Embed the schema-doc sections and the few-shot questions into data/index.json.

    uv run --env-file .env scripts/build_index.py

Re-run whenever data/schema_docs.md or data/examples.jsonl changes (the Retriever refuses a stale index).
"""

import sys
from collections import Counter

from thai_shop_agent.config import EMBED_MODEL, INDEX_PATH
from thai_shop_agent.llm import Embedder
from thai_shop_agent.retrieval import build_index, load_chunks, source_hashes


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    chunks = load_chunks()
    index = build_index(Embedder(), chunks, source_hashes())
    kinds = Counter(c.kind for c in chunks)
    print(f"{len(chunks)} chunks {dict(kinds)} embedded with {EMBED_MODEL} (dim {index['dim']})")
    print(f"-> {INDEX_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
