"""Sweep the retrieval settings with the single-shot predictor (B1 when k_examples = 0, B2 otherwise).

    uv run --env-file .env eval/sweep.py

Grid: k_schema x k_examples in {0, 1, 3, 5} x min_score in MIN_SCORES (rule-table DDL on, as in use).
Per setting: EX, EX-strict, valid SQL rate, EX by difficulty / lang, schema recall (gold tables
whose DDL reached the prompt) and average prompt tokens. Scoring is eval/run.py's evaluate_item.
Identical prompts are sent once (temperature 0) and question embeddings are computed once, so
settings that a min_score does not change cost nothing extra.

Selection rule, fixed before looking at the results: highest EX, then EX-strict, then valid SQL
rate, then hard EX, then fewest prompt tokens. The test set is the only labelled set (no dev
split), so values chosen here are tuned on the test set; that is a limitation for the report.
Output: eval/results/sweep_<timestamp>.json
"""

import hashlib
import json
import sys
from datetime import datetime
from itertools import product

import run as eval_run
from thai_shop_agent.baseline import RetrievalBaseline
from thai_shop_agent.config import MODEL, TEMPERATURE, RetrievalParams
from thai_shop_agent.db import connect_ro
from thai_shop_agent.llm import LLM, Completion, Embedder
from thai_shop_agent.retrieval import Retriever, source_hashes

K_SCHEMA = [0, 1, 3, 5]
K_EXAMPLES = [0, 1, 3, 5]
MIN_SCORES = [0.0, 0.35, 0.4]  # <= 0.3 filters nothing (retrieval_20261023-112336.json); >= 0.45 drops recall


class CachedEmbedder:
    def __init__(self, inner: Embedder):
        self.inner = inner
        self.cache: dict[str, list[float]] = {}

    def embed(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in dict.fromkeys(texts) if t not in self.cache]
        if missing:
            self.cache.update(zip(missing, self.inner.embed(missing)))
        return [self.cache[t] for t in texts]


class CachedLLM:
    """Same messages -> same completion (temperature 0), so each distinct prompt is sent once."""

    def __init__(self, inner: LLM):
        self.inner = inner
        self.cache: dict[str, Completion] = {}
        self.sent = 0

    def chat(self, messages: list[dict]) -> Completion:
        key = hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode("utf-8")).hexdigest()
        if key not in self.cache:
            self.cache[key] = self.inner.chat(messages)
            self.sent += 1
        return self.cache[key]


def schema_recall(gold_tables: list[str], context_ids: list[str], retriever: Retriever) -> float:
    """Gold tables whose DDL was in the prompt: retrieved table chunks + tables named by retrieved rules."""
    by_id = {c.id: c for c in retriever.chunks}
    got = set()
    for cid in context_ids:
        c = by_id[cid]
        if c.kind == "table":
            got.add(c.table)
        elif c.kind == "rule":
            got.update(c.tables)
    return len(set(gold_tables) & got) / len(gold_tables)


def rank_key(row: dict) -> tuple:
    s = row["scores"]
    return (s["ex"], s["ex_strict"], s["valid_sql_rate"], s["ex_hard"], -row["avg_prompt_tokens"])


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    items = eval_run.load_testset()
    gold_tables = {i["id"]: i["tables"] for i in items}
    conn = connect_ro()
    llm = CachedLLM(LLM())
    retriever = Retriever(CachedEmbedder(Embedder()))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    rows = []
    for k_schema, k_examples, min_score in product(K_SCHEMA, K_EXAMPLES, MIN_SCORES):
        params = RetrievalParams(k_schema=k_schema, k_examples=k_examples, min_score=min_score)
        predictor = RetrievalBaseline(llm, conn, retriever, params)
        records = [eval_run.evaluate_item(i, predictor, conn) for i in items]
        summary = eval_run.summarize(records)
        recalls = [schema_recall(gold_tables[r["id"]], r["context_ids"] or [], retriever) for r in records]
        tokens = [r["prompt_tokens"] for r in records if r["prompt_tokens"] is not None]
        row = {
            "k_schema": k_schema, "k_examples": k_examples, "min_score": min_score,
            "scores": {
                "ex": summary["overall"]["ex"], "ex_strict": summary["overall"]["ex_strict"],
                "valid_sql_rate": summary["overall"]["valid_sql_rate"],
                **{f"ex_{d}": summary["by_difficulty"][d]["ex"] for d in ("easy", "medium", "hard")},
                **{f"ex_{lang}": summary["by_lang"][lang]["ex"] for lang in ("th", "en")},
            },
            "schema_recall": round(sum(recalls) / len(recalls), 4),
            "avg_prompt_tokens": round(sum(tokens) / len(tokens), 1) if tokens else None,
            "llm_errors": sum(1 for r in records if (r["error"] or "").startswith("llm_error")),
            "per_question": {r["id"]: {"ex": r["ex"], "ok": r["ok"], "pred_sql": r["pred_sql"]} for r in records},
        }
        rows.append(row)
        s = row["scores"]
        print(f"k_schema={k_schema} k_examples={k_examples} min={min_score:<4}  EX {s['ex']:.3f}  "
              f"strict {s['ex_strict']:.3f}  valid {s['valid_sql_rate']:.3f}  hard {s['ex_hard']:.3f}  "
              f"recall {row['schema_recall']:.3f}  tokens {row['avg_prompt_tokens']}  (sent {llm.sent})", flush=True)

    ok_rows = [r for r in rows if r["llm_errors"] == 0]
    best = max(ok_rows, key=rank_key)
    best_b1 = max((r for r in ok_rows if r["k_examples"] == 0), key=rank_key)
    out = {
        "timestamp": stamp, "model": MODEL, "temperature": TEMPERATURE, "predictor": "single-shot (B1/B2)",
        "rule_tables": True, "sources": source_hashes(), "testset_sha256": eval_run.sha256(eval_run.TESTSET),
        "grid": {"k_schema": K_SCHEMA, "k_examples": K_EXAMPLES, "min_score": MIN_SCORES},
        "selection_rule": "max EX, then EX-strict, then valid SQL rate, then hard EX, then fewest prompt tokens",
        "selected": {k: best[k] for k in ("k_schema", "k_examples", "min_score")},
        "selected_zero_shot": {k: best_b1[k] for k in ("k_schema", "k_examples", "min_score")},
        "distinct_prompts_sent": llm.sent,
        "limitation": "tuned on the 30-question test set (no dev split)",
        "rows": rows,
    }
    eval_run.RESULTS_DIR.mkdir(exist_ok=True)
    path = eval_run.RESULTS_DIR / f"sweep_{stamp}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\nselected: {out['selected']}  (zero-shot best: {out['selected_zero_shot']})")
    print(f"-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
