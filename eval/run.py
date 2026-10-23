"""Run one system config over eval/testset.jsonl and write per-question results + a summary.

Every predicted SQL goes through sql_guard.prepare (clean -> guard -> LIMIT 100) before it is
executed, for every config including b0; a guard rejection counts as invalid SQL. ``pred_sql``
is the model's query, ``exec_sql`` what was actually run.

    uv run --env-file .env eval/run.py --config b0      # b1 | b2 | full
    uv run --env-file .env eval/run.py --config b2 --k-schema 3 --k-examples 3 --min-score 0.0   # override

Adding a config (b1, b2, full) = adding a factory to CONFIGS that takes (conn, retrieval params)
and returns an object with ``predict(question) -> Prediction``; the scoring and reporting below
stay the same. Retrieval params default to config.DEFAULT_RETRIEVAL (chosen by eval/sweep.py);
b1 always uses k_examples = 0.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Protocol

from thai_shop_agent.agent import Agent
from thai_shop_agent.baseline import Prediction, RetrievalBaseline, ZeroShotBaseline
from thai_shop_agent.config import DEFAULT_RETRIEVAL, MODEL, QUERY_TIMEOUT_S, TEMPERATURE, RetrievalParams
from thai_shop_agent.db import DB_PATH, connect_ro, run_query
from thai_shop_agent.eval_metrics import ex_match, ex_strict
from thai_shop_agent.llm import LLM, Embedder, redact, wait_seconds
from thai_shop_agent.retrieval import Retriever
from thai_shop_agent.sql_guard import GuardError, prepare

EVAL_DIR = Path(__file__).resolve().parent
TESTSET = EVAL_DIR / "testset.jsonl"
RESULTS_DIR = EVAL_DIR / "results"


class Predictor(Protocol):
    def predict(self, question: str) -> Prediction: ...


class AgentPredictor:
    """Runs the agent and reads its last event; pred SQL = FinalAnswer.sql ("" when none ran -> invalid)."""

    def __init__(self, agent: Agent):
        self.agent = agent

    def predict(self, question: str) -> Prediction:
        last = None
        for last in self.agent.run(question):
            pass
        if last is None or last["type"] == "error":
            raise RuntimeError(last["message"] if last else "agent produced no events")
        u = last["usage"]
        return Prediction(sql=last["sql"] or "", raw_output=last["answer"], prompt_tokens=u["prompt_tokens"],
                          completion_tokens=u["completion_tokens"], llm_calls=u["llm_calls"],
                          context_ids=last["citations"]["chunks"], steps=last["steps"],
                          stopped_reason=last["stopped_reason"])


CONFIGS: dict[str, Callable[[sqlite3.Connection, RetrievalParams | None], Predictor]] = {
    "b0": lambda conn, params: ZeroShotBaseline(LLM(), conn),
    "b1": lambda conn, params: RetrievalBaseline(LLM(), conn, Retriever(Embedder()), params),
    "b2": lambda conn, params: RetrievalBaseline(LLM(), conn, Retriever(Embedder()), params),
    "full": lambda conn, params: AgentPredictor(Agent(LLM(), Retriever(Embedder()), conn, params)),
}
RETRIEVAL_CONFIGS = {"b1", "b2", "full"}


def load_testset(path: Path = TESTSET) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def evaluate_item(item: dict, predictor: Predictor, conn: sqlite3.Connection,
                  timeout_s: float = QUERY_TIMEOUT_S) -> dict:
    gold_cols, gold_rows = run_query(conn, item["gold_sql"], timeout_s)
    rec = {"id": item["id"], "lang": item["lang"], "difficulty": item["difficulty"], "question": item["question"],
           "pred_sql": None, "exec_sql": None, "ok": False, "error": None, "ex": False, "ex_strict": False, "latency_s": None,
           "prompt_tokens": None, "completion_tokens": None, "llm_calls": 0, "raw_output": None,
           "context_ids": None, "steps": None, "stopped_reason": None, "retry_wait_s": 0.0}
    t0, w0 = time.perf_counter(), wait_seconds()

    def finish() -> dict:
        # latency excludes time slept on proxy rate limits / retries, which is recorded separately
        rec["retry_wait_s"] = round(wait_seconds() - w0, 3)
        rec["latency_s"] = round(time.perf_counter() - t0 - rec["retry_wait_s"], 3)
        return rec

    try:
        pred = predictor.predict(item["question"])
    except Exception as e:  # noqa: BLE001 - an API failure is recorded, not fatal for the run
        rec["error"] = redact(f"llm_error: {type(e).__name__}: {e}")
        return finish()
    rec.update(pred_sql=pred.sql, raw_output=pred.raw_output, prompt_tokens=pred.prompt_tokens,
               completion_tokens=pred.completion_tokens, llm_calls=pred.llm_calls,
               context_ids=pred.context_ids or None, steps=pred.steps, stopped_reason=pred.stopped_reason)
    try:
        rec["exec_sql"] = prepare(pred.sql)
    except GuardError as e:
        rec["error"] = f"guard: {e}"
        return finish()
    try:
        pred_cols, pred_rows = run_query(conn, rec["exec_sql"], timeout_s)
    except Exception as e:  # noqa: BLE001 - any SQL failure makes the prediction invalid
        rec["error"] = f"{type(e).__name__}: {e}"
    else:
        rec["ok"] = True
        args = (gold_rows, pred_rows, len(gold_cols), len(pred_cols), item["order_matters"])
        rec["ex"], rec["ex_strict"] = ex_match(*args), ex_strict(*args)
    return finish()


def _scores(records: list[dict]) -> dict:
    n = len(records)
    if n == 0:
        return {"n": 0}
    latencies = [r["latency_s"] for r in records if r["latency_s"] is not None]
    return {
        "n": n,
        "ex": round(sum(r["ex"] for r in records) / n, 4),
        "ex_strict": round(sum(r["ex_strict"] for r in records) / n, 4),
        "valid_sql_rate": round(sum(r["ok"] for r in records) / n, 4),
        "avg_latency_s": round(sum(latencies) / len(latencies), 3) if latencies else None,
    }


def summarize(records: list[dict]) -> dict:
    out = {"overall": _scores(records)}
    for key, values in (("difficulty", ["easy", "medium", "hard"]), ("lang", ["th", "en"])):
        out[f"by_{key}"] = {v: _scores([r for r in records if r[key] == v]) for v in values}
    return out


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def print_table(summary: dict) -> None:
    print(f"\n{'group':<20}{'n':>4}{'EX':>8}{'EX-strict':>11}{'valid SQL':>11}{'latency s':>11}")
    rows = [("overall", summary["overall"])]
    rows += [(f"{k}={v}", s) for k in ("difficulty", "lang") for v, s in summary[f"by_{k}"].items()]
    for name, s in rows:
        if s["n"]:
            print(f"{name:<20}{s['n']:>4}{s['ex']:>8.3f}{s['ex_strict']:>11.3f}{s['valid_sql_rate']:>11.3f}"
                  f"{s['avg_latency_s']:>11.2f}")


def retrieval_params(ap: argparse.ArgumentParser, args: argparse.Namespace) -> RetrievalParams | None:
    if args.config not in RETRIEVAL_CONFIGS:
        return None
    d = DEFAULT_RETRIEVAL
    k_schema = d.k_schema if args.k_schema is None else args.k_schema
    min_score = d.min_score if args.min_score is None else args.min_score
    k_examples = 0 if args.config == "b1" else (d.k_examples if args.k_examples is None else args.k_examples)
    if args.config == "b2" and k_examples < 1:
        ap.error("--config b2 needs --k-examples >= 1 (k_examples = 0 is b1)")
    # full starts from the B2 context; --k-examples 0 is allowed there (the sweep may pick it)
    return RetrievalParams(k_schema=k_schema, k_examples=k_examples, min_score=min_score,
                           rule_tables=not args.no_rule_tables)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Evaluate a text-to-SQL config on the test set")
    ap.add_argument("--config", required=True, choices=sorted(CONFIGS))
    ap.add_argument("--ids", nargs="*", help="only run these question ids (smoke test)")
    ap.add_argument("--timeout", type=float, default=QUERY_TIMEOUT_S, help="per-query timeout in seconds")
    ap.add_argument("--k-schema", type=int, help="table/rule chunks to retrieve (b1, b2)")
    ap.add_argument("--k-examples", type=int, help="examples to retrieve (b2, full; b1 is always 0)")
    ap.add_argument("--min-score", type=float, help="cosine threshold for retrieval (b1, b2)")
    ap.add_argument("--no-rule-tables", action="store_true", help="only DDL of retrieved table chunks")
    args = ap.parse_args()
    params = retrieval_params(ap, args)

    items = load_testset()
    if args.ids:
        items = [i for i in items if i["id"] in set(args.ids)]
    conn = connect_ro()
    predictor = CONFIGS[args.config](conn, params)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    RESULTS_DIR.mkdir(exist_ok=True)
    results_path = RESULTS_DIR / f"{args.config}_{stamp}.jsonl"
    records = []
    with results_path.open("w", encoding="utf-8", newline="\n") as f:
        for item in items:
            rec = evaluate_item(item, predictor, conn, args.timeout)
            records.append(rec)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            mark = "EX " if rec["ex"] else ("ok " if rec["ok"] else "ERR")
            print(f"{rec['id']} [{mark}] {rec['latency_s']:>6.2f}s  {(rec['pred_sql'] or rec['error'] or '')[:90]!r}")

    summary = {
        "config": args.config,
        "timestamp": stamp,
        "model": MODEL,
        "temperature": TEMPERATURE,
        "results_file": results_path.name,
        "testset_sha256": sha256(TESTSET),
        "db_sha256": sha256(DB_PATH),
        "subset_ids": args.ids or None,
        "retrieval_params": asdict(params) if params else None,
        **summarize(records),
    }
    # a per-run copy keeps earlier runs comparable; {config}_summary.json is always the latest
    summary_path = RESULTS_DIR / f"{args.config}_summary.json"
    for path in (RESULTS_DIR / f"{args.config}_{stamp}_summary.json", summary_path):
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_table(summary)
    print(f"\n-> {results_path}\n-> {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
