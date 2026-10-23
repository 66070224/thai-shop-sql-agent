"""Collect every config's runs into one report: eval/results/comparison.md

    uv run eval/compare.py

Included runs: full test set, current test set hash, SQL executed through the guard, no
proxy/LLM errors, and for b1 / b2 / full the default retrieval params in config.py. Every
included run is listed (two runs of the same config are both shown, never only the better one);
every other run is listed separately with the reason it was left out. Cost columns (latency, llm_calls, tokens) are averaged from the per-question
results. Also shows the sweep that chose the params and the retrieval recall@k.
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

from thai_shop_agent.config import DEFAULT_RETRIEVAL

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
TESTSET = EVAL_DIR / "testset.jsonl"
OUT = RESULTS_DIR / "comparison.md"
CONFIG_ORDER = ["b0", "b1", "b2", "full"]
GROUPS = [("difficulty", ["easy", "medium", "hard"]), ("lang", ["th", "en"])]


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def fmt(v: float | None, digits: int = 3) -> str:
    return "–" if v is None else f"{v:.{digits}f}"


def expected_params(config: str) -> dict | None:
    if config == "b0":
        return None
    params = asdict(DEFAULT_RETRIEVAL)
    if config == "b1":
        params["k_examples"] = 0
    return params


def cost(records: list[dict]) -> dict:
    return {
        "latency": mean([r["latency_s"] for r in records]),
        "llm_calls": mean([r["llm_calls"] for r in records]),
        "prompt_tokens": mean([r["prompt_tokens"] for r in records]),
        "completion_tokens": mean([r["completion_tokens"] for r in records]),
    }


def collect(testset_sha: str) -> tuple[list[dict], list[dict]]:
    used, other = [], []
    for path in sorted(RESULTS_DIR.glob("*_*_summary.json")):
        s = json.loads(path.read_text(encoding="utf-8"))
        results = RESULTS_DIR / s["results_file"]
        if s.get("subset_ids") or s["testset_sha256"] != testset_sha or not results.exists():
            continue
        records = load_jsonl(results)
        if not records or "exec_sql" not in records[0]:  # ran before the guard existed
            continue
        run = {"summary": s, "records": records, "cost": cost(records), "why": None}
        llm_errors = [r["id"] for r in records if (r["error"] or "").startswith("llm_error")]
        if s.get("retrieval_params") != expected_params(s["config"]):
            run["why"] = f"retrieval params {s.get('retrieval_params')}"
        elif llm_errors:  # a proxy failure is not a model answer; the run is listed, not scored with the rest
            run["why"] = f"proxy/LLM error on {', '.join(llm_errors)} (scored as wrong in this run)"
        (other if run["why"] else used).append(run)
    key = lambda r: (CONFIG_ORDER.index(r["summary"]["config"]), r["summary"]["timestamp"])  # noqa: E731
    return sorted(used, key=key), sorted(other, key=key)


def run_label(runs: list[dict]) -> list[str]:
    seen: dict[str, int] = {}
    labels = []
    for r in runs:
        c = r["summary"]["config"]
        seen[c] = seen.get(c, 0) + 1
        labels.append(f"{c} #{seen[c]} ({r['summary']['timestamp']})")
    return labels


def overall_table(runs: list[dict]) -> list[str]:
    lines = ["| run | EX | EX-strict | valid SQL | hard EX | th EX | en EX | latency s | llm_calls | prompt tok | completion tok |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for label, r in zip(run_label(runs), runs):
        s, c = r["summary"], r["cost"]
        lines.append(f"| {label} | {fmt(s['overall']['ex'])} | {fmt(s['overall']['ex_strict'])} | "
                     f"{fmt(s['overall']['valid_sql_rate'])} | {fmt(s['by_difficulty']['hard']['ex'])} | "
                     f"{fmt(s['by_lang']['th']['ex'])} | {fmt(s['by_lang']['en']['ex'])} | {fmt(c['latency'], 2)} | "
                     f"{fmt(c['llm_calls'], 2)} | {fmt(c['prompt_tokens'], 0)} | {fmt(c['completion_tokens'], 0)} |")
    return lines


def group_table(runs: list[dict], key: str, values: list[str]) -> list[str]:
    lines = [f"| run | {key} | n | EX | EX-strict | valid SQL | latency s | llm_calls | prompt tok |",
             "|---|---|---|---|---|---|---|---|---|"]
    for label, r in zip(run_label(runs), runs):
        for v in values:
            s = r["summary"][f"by_{key}"][v]
            c = cost([x for x in r["records"] if x[key] == v])
            lines.append(f"| {label} | {v} | {s['n']} | {fmt(s['ex'])} | {fmt(s['ex_strict'])} | "
                         f"{fmt(s['valid_sql_rate'])} | {fmt(c['latency'], 2)} | {fmt(c['llm_calls'], 2)} | "
                         f"{fmt(c['prompt_tokens'], 0)} |")
    return lines


def repeatability(runs: list[dict]) -> list[str]:
    lines = []
    for config in CONFIG_ORDER:
        rs = [r for r in runs if r["summary"]["config"] == config]
        if len(rs) < 2:
            lines.append(f"- {config}: {len(rs)} run(s) with these settings")
            continue
        base = {x["id"]: (x["ex"], x["ex_strict"], x["ok"]) for x in rs[0]["records"]}
        diffs = sorted({x["id"] for r in rs[1:] for x in r["records"] if base[x["id"]] != (x["ex"], x["ex_strict"], x["ok"])})
        sql_diffs = sorted({x["id"] for r in rs[1:] for x in r["records"]
                            if x["pred_sql"] != next(y["pred_sql"] for y in rs[0]["records"] if y["id"] == x["id"])})
        verdict = "same score on every question" if not diffs else f"score differs on {', '.join(diffs)}"
        lines.append(f"- {config}: {len(rs)} runs, {verdict}; predicted SQL text differs on "
                     f"{len(sql_diffs)} question(s){': ' + ', '.join(sql_diffs) if sql_diffs else ''}")
    return lines


def sweep_section() -> list[str]:
    paths = sorted(RESULTS_DIR.glob("sweep_*.json"))
    if not paths:
        return ["(no sweep results)"]
    sw = json.loads(paths[-1].read_text(encoding="utf-8"))
    lines = [f"File: `{paths[-1].name}` · predictor: {sw['predictor']} · rule-table DDL on · "
             f"{sw['distinct_prompts_sent']} distinct prompts sent",
             f"Selection rule: {sw['selection_rule']}.",
             f"**Selected:** k_schema={sw['selected']['k_schema']}, k_examples={sw['selected']['k_examples']}, "
             f"min_score={sw['selected']['min_score']} · best zero-shot setting: k_schema="
             f"{sw['selected_zero_shot']['k_schema']}, min_score={sw['selected_zero_shot']['min_score']}",
             f"Limitation: {sw['limitation']}.", "",
             "| k_schema | k_examples | min_score | EX | EX-strict | valid | easy | medium | hard | th | en | recall | prompt tok |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sw["rows"]:
        s = r["scores"]
        mark = " **←**" if all(r[k] == sw["selected"][k] for k in ("k_schema", "k_examples", "min_score")) else ""
        lines.append(f"| {r['k_schema']} | {r['k_examples']} | {r['min_score']} | {fmt(s['ex'])}{mark} | "
                     f"{fmt(s['ex_strict'])} | {fmt(s['valid_sql_rate'])} | {fmt(s['ex_easy'])} | {fmt(s['ex_medium'])} | "
                     f"{fmt(s['ex_hard'])} | {fmt(s['ex_th'])} | {fmt(s['ex_en'])} | {fmt(r['schema_recall'])} | "
                     f"{fmt(r['avg_prompt_tokens'], 0)} |")
    return lines


def retrieval_section() -> list[str]:
    paths = sorted(RESULTS_DIR.glob("retrieval_*.json"))
    if not paths:
        return ["(no retrieval eval results)"]
    rv = json.loads(paths[-1].read_text(encoding="utf-8"))
    lines = [f"File: `{paths[-1].name}` · min_score 0.0 · recall = gold tables whose DDL reaches the prompt", "",
             "| mode | " + " | ".join(f"k={k}" for k in rv["ks"]) + " |", "|---|" + "---|" * len(rv["ks"])]
    for mode, grid in rv["modes"].items():
        lines.append(f"| {mode} | " + " | ".join(fmt(grid["0.0"][str(k)]["overall"]) for k in rv["ks"]) + " |")
    return lines


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    import hashlib
    testset_sha = hashlib.sha256(TESTSET.read_bytes()).hexdigest()
    runs, other = collect(testset_sha)
    p = DEFAULT_RETRIEVAL
    md = ["# Comparison of configs", "",
          "Generated by `uv run eval/compare.py` from the run summaries in `eval/results/`. "
          "Numbers come only from runs that were executed; every run with the default settings is listed.", "",
          f"Default retrieval (config.py): k_schema={p.k_schema}, k_examples={p.k_examples} (b1: 0), "
          f"min_score={p.min_score}, rule-table DDL={'on' if p.rule_tables else 'off'}; agent max_steps=5; "
          "temperature 0.", "",
          "## Overall", "", *overall_table(runs), "",
          "## By difficulty", "", *group_table(runs, "difficulty", ["easy", "medium", "hard"]), "",
          "## By language", "", *group_table(runs, "lang", ["th", "en"]), "",
          "## Repeatability", "", *repeatability(runs), "",
          "## Sweep used to choose k / min_score", "", *sweep_section(), "",
          "## Retrieval recall@k (no chat model)", "", *retrieval_section(), ""]
    if other:
        md += ["## Other runs (not used above)", "", *overall_table(other), "",
               *(f"- {label}: {r['why']}" for label, r in zip(run_label(other), other)), ""]
    OUT.write_text("\n".join(md), encoding="utf-8", newline="\n")
    print("\n".join(overall_table(runs)))
    print(f"\n-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
