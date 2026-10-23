"""Check that the course proxy supports what the app needs before building on it.

    uv run --env-file .env scripts/check_proxy.py

1. embeddings: bge-m3 returns one 1024-dim vector per input, in input order (batch).
2. tool calling: the chat model answers with native ``tool_calls`` when given ``tools=[...]``.
Exit code 0 only if both pass. The API key is never printed.
"""

import json
import sys

from openai import OpenAI

EMBED_MODEL = "bge-m3"
CHAT_MODEL = "gemma-4-E4B-it"
EXPECTED_DIM = 1024

TOOLS = [{
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": "Execute one read-only SQLite SELECT query on the shop database and return the rows.",
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "a single SQLite SELECT statement"}},
            "required": ["sql"],
        },
    },
}]


def check_embeddings(client: OpenAI) -> bool:
    texts = ["ยอดขายรวมปี 2024", "How many customers live in Bangkok?", "order_items"]
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    dims = [len(d.embedding) for d in resp.data]
    indexes = [d.index for d in resp.data]
    ok = len(resp.data) == len(texts) and all(n == EXPECTED_DIM for n in dims) and indexes == list(range(len(texts)))
    print(f"[{'PASS' if ok else 'FAIL'}] embeddings {EMBED_MODEL}: {len(resp.data)} vectors, dims={dims}, index={indexes}")
    return ok


def check_tool_calls(client: OpenAI) -> bool:
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        temperature=0,
        tools=TOOLS,
        messages=[
            {"role": "system", "content": "You answer questions about a shop database by calling tools."},
            {"role": "user", "content": "How many rows are in the orders table? Use the run_sql tool."},
        ],
    )
    msg = resp.choices[0].message
    calls = msg.tool_calls or []
    ok = False
    for c in calls:
        try:
            args = json.loads(c.function.arguments)
        except json.JSONDecodeError:
            args = None
        print(f"  tool_call: name={c.function.name!r} args={args!r}")
        ok = ok or (c.function.name == "run_sql" and isinstance(args, dict) and "sql" in args)
    if not calls:
        print(f"  no tool_calls; finish_reason={resp.choices[0].finish_reason!r} content={(msg.content or '')[:200]!r}")
    print(f"[{'PASS' if ok else 'FAIL'}] native tool_calls {CHAT_MODEL}")
    return ok


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    client = OpenAI()  # OPENAI_API_KEY / OPENAI_BASE_URL from the environment
    results = []
    for check in (check_embeddings, check_tool_calls):
        try:
            results.append(check(client))
        except Exception as e:  # noqa: BLE001 - report and keep checking the other capability
            print(f"[FAIL] {check.__name__}: {type(e).__name__}: {e}")
            results.append(False)
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
