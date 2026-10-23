"""Schema recall@k of the retriever on the test set (no chat model is called).

    uv run --env-file .env eval/retrieval_eval.py

recall of one question = |gold tables ∩ tables of the retrieved table chunks| / |gold tables|.
Measured for k = 1..8 and several min_score levels, overall and by lang / difficulty, in three modes:
  mixed       tables and rules ranked together (the default prompt assembly; rules use up slots)
  table_only  only table chunks ranked
  mixed_rule_tables  same ranking as mixed, but a retrieved rule chunk also counts the tables its
              text names (measures the alternative "add DDL for tables named in retrieved rules")
Also records how many slots rules took, and each question's ranked list, so the k / min_score
choice in eval/sweep.py can be checked against these numbers.
Output: eval/results/retrieval_<timestamp>.json
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from thai_shop_agent.llm import Embedder
from thai_shop_agent.retrieval import SCHEMA_KINDS, Hit, Retriever, source_hashes
from validate_testset import load as load_testset

RESULTS_DIR = Path(__file__).resolve().parent / "results"
KS = list(range(1, 9))
MIN_SCORES = [0.0, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6]
MODES = {"mixed": SCHEMA_KINDS, "table_only": "table", "mixed_rule_tables": SCHEMA_KINDS}
TABLES = ["orders", "order_items", "products", "categories", "customers", "payments", "reviews", "coupons"]
GROUPS = (("difficulty", ["easy", "medium", "hard"]), ("lang", ["th", "en"]))


def tables_named(text: str) -> set[str]:
    return {t for t in TABLES if re.search(rf"\b{t}\b", text)}


def recall(gold_tables: list[str], hits: list[Hit], rule_tables: bool = False) -> float:
    got = {h.chunk.table for h in hits if h.chunk.kind == "table"}
    if rule_tables:
        got |= {t for h in hits if h.chunk.kind == "rule" for t in tables_named(h.chunk.text)}
    return len(set(gold_tables) & got) / len(gold_tables)


def mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def score_grid(items: list[dict], ranked: dict[str, list[Hit]], rule_tables: bool = False) -> dict:
    """{min_score: {k: {"overall", "difficulty=..", "lang=..", "avg_chunks", "avg_rule_chunks"}}}"""
    grid = {}
    for min_score in MIN_SCORES:
        per_k = {}
        for k in KS:
            hits = {i["id"]: [h for h in ranked[i["id"]] if h.score >= min_score][:k] for i in items}
            rec = {i["id"]: recall(i["tables"], hits[i["id"]], rule_tables) for i in items}
            row = {"overall": mean(list(rec.values()))}
            for key, values in GROUPS:
                for v in values:
                    row[f"{key}={v}"] = mean([rec[i["id"]] for i in items if i[key] == v])
            row["full_recall_rate"] = mean([float(r == 1.0) for r in rec.values()])
            row["avg_chunks"] = mean([len(h) for h in hits.values()])
            row["avg_rule_chunks"] = mean([sum(x.chunk.kind == "rule" for x in h) for h in hits.values()])
            per_k[str(k)] = row
        grid[str(min_score)] = per_k
    return grid


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    items = load_testset()
    retriever = Retriever(Embedder())
    vectors = retriever.embedder.embed([i["question"] for i in items])  # one batched call

    out = {"timestamp": datetime.now().strftime("%Y%m%d-%H%M%S"), "model": retriever.model,
           "sources": source_hashes(), "ks": KS, "min_scores": MIN_SCORES, "modes": {}, "per_question": []}
    ranked_by_mode = {mode: {i["id"]: retriever.rank(v, kind) for i, v in zip(items, vectors)}
                      for mode, kind in MODES.items()}
    for mode in MODES:
        out["modes"][mode] = score_grid(items, ranked_by_mode[mode], rule_tables=mode == "mixed_rule_tables")
    for i in items:
        mixed = ranked_by_mode["mixed"][i["id"]]
        examples = retriever.rank(vectors[items.index(i)], "example")[:5]
        out["per_question"].append({
            "id": i["id"], "lang": i["lang"], "difficulty": i["difficulty"], "gold_tables": i["tables"],
            "mixed_ranking": [[h.chunk.id, round(h.score, 4)] for h in mixed],
            "top_examples": [[h.chunk.id, round(h.score, 4)] for h in examples],
        })

    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"retrieval_{out['timestamp']}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    for mode in MODES:
        print(f"\n[{mode}] overall recall@k (rows: min_score, cols: k)")
        print(f"{'min_score':>9}" + "".join(f"{k:>7}" for k in KS))
        for ms, per_k in out["modes"][mode].items():
            print(f"{ms:>9}" + "".join(f"{per_k[str(k)]['overall']:>7.3f}" for k in KS))
    print("\n[mixed, min_score 0.0] by group at k = 3 / 5 / 8; avg rule chunks in top k")
    grid = out["modes"]["mixed"]["0.0"]
    for g in ["overall"] + [f"{key}={v}" for key, values in GROUPS for v in values]:
        print(f"  {g:<18}" + "".join(f"{grid[str(k)][g]:>8.3f}" for k in (3, 5, 8)))
    print(f"  {'avg_rule_chunks':<18}" + "".join(f"{grid[str(k)]['avg_rule_chunks']:>8.2f}" for k in (3, 5, 8)))
    scores = [s for q in out["per_question"] for _, s in q["mixed_ranking"]]
    top1 = [q["mixed_ranking"][0][1] for q in out["per_question"]]
    print(f"\nscore range all={min(scores):.3f}..{max(scores):.3f}; top-1 {min(top1):.3f}..{max(top1):.3f}")
    print(f"-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
