"""Before/after report for pinning two rule chunks in every prompt: eval/results/before_after_pinned_rules.md

    uv run eval/before_after.py --before <results .jsonl ...> --after <results .jsonl ...>

Each argument is a per-run results file written by eval/run.py (its ``<stem>_summary.json`` must sit
next to it). Runs are grouped by config (b2, full) and by side. Every table in the report comes from
these files, the few-shot bank (data/examples.jsonl) and the latest retrieval eval file; nothing is
typed in by hand.

Decision rule (fixed before the after runs, see CLAUDE.md), applied to the ``full`` runs without
proxy/LLM errors:
  1. EX of every after run > EX of every before run
  2. valid SQL rate of every after run >= valid SQL rate of every before run
  3. questions that got worse < questions that got better, where a question got better (worse) when
     it is EX-correct in more (fewer) after runs than before runs
"""

import argparse
import json
import re
import sys
from pathlib import Path

from thai_shop_agent.config import DEFAULT_RETRIEVAL, EXAMPLES, PINNED_RULES

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
OUT = RESULTS_DIR / "before_after_pinned_rules.md"
CONFIGS = ["b2", "full"]
SIDES = ["before", "after"]
SALES_FORMULA = re.compile(r"quantity\s*\*\s*(\w+\.)?unit_price\s*-\s*(\w+\.)?discount", re.I)
RECALL_K, RECALL_MIN_SCORE = DEFAULT_RETRIEVAL.k_schema, DEFAULT_RETRIEVAL.min_score


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def fmt(v: float | None, digits: int = 3) -> str:
    return "–" if v is None else f"{v:.{digits}f}"


def load_run(path: Path, side: str) -> dict:
    summary = json.loads(path.with_name(f"{path.stem}_summary.json").read_text(encoding="utf-8"))
    records = load_jsonl(path)
    if summary.get("subset_ids"):
        raise SystemExit(f"{path.name} is a subset run")
    pinned = tuple((summary.get("retrieval_params") or {}).get("pinned_rules", ()))
    if pinned != (PINNED_RULES if side == "after" else ()):
        raise SystemExit(f"{path.name}: pinned_rules={pinned} does not belong on the {side} side")
    return {"side": side, "config": summary["config"], "path": path, "summary": summary, "records": records,
            "by_id": {r["id"]: r for r in records},
            "llm_errors": [r["id"] for r in records if (r["error"] or "").startswith("llm_error")]}


def label(run: dict) -> str:
    return f"{run['config']} {run['side']} ({run['summary']['timestamp']})"


def examples_evidence() -> list[str]:
    ex = load_jsonl(EXAMPLES)
    traps = lambda t: [e["id"] for e in ex if t in e["traps"]]  # noqa: E731
    formula = [e["id"] for e in ex if SALES_FORMULA.search(e["sql"])]
    either = [e["id"] for e in ex if {"cancelled_refunded", "province_aliases"} & set(e["traps"])
              or SALES_FORMULA.search(e["sql"])]
    n = len(ex)
    return [f"Counted from `data/examples.jsonl` ({n} few-shot examples; written before the test set was "
            "analysed, not the test set):", "",
            "| evidence | examples | share |", "|---|---|---|",
            f"| trap `cancelled_refunded` (must drop cancelled/refunded — part of the sales rule) | "
            f"{len(traps('cancelled_refunded'))} | {len(traps('cancelled_refunded')) / n:.0%} |",
            f"| SQL uses the sales formula `quantity * unit_price - discount` | {len(formula)} | {len(formula) / n:.0%} |",
            f"| trap `province_aliases` | {len(traps('province_aliases'))} | {len(traps('province_aliases')) / n:.0%} |",
            f"| any of the three | {len(either)} | {len(either) / n:.0%} |", "",
            "For comparison, the other traps: " + ", ".join(
                f"`{t}` {len(traps(t))}" for t in ("duplicate_customers", "null_dates", "multi_payment")) + "."]


def recall_numbers() -> tuple[str, dict[str, float | None]]:
    paths = [p for p in sorted(RESULTS_DIR.glob("retrieval_*.json"))
             if "pinned_rule_tables" in json.loads(p.read_text(encoding="utf-8"))["modes"]]
    if not paths:
        return "(no retrieval eval with a pinned mode)", {"before": None, "after": None}
    modes = json.loads(paths[-1].read_text(encoding="utf-8"))["modes"]
    cell = lambda m: modes[m][str(RECALL_MIN_SCORE)][str(RECALL_K)]["overall"]  # noqa: E731
    return paths[-1].name, {"before": cell("mixed_rule_tables"), "after": cell("pinned_rule_tables")}


def run_table(runs: list[dict], recall: dict) -> list[str]:
    lines = ["| run | EX | EX-strict | valid SQL | hard EX | th EX | en EX | latency s | llm_calls | prompt tok | "
             "completion tok | schema recall@k | proxy errors | 429 wait s (not in latency) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        s, rec = r["summary"], r["records"]
        lines.append(
            f"| {label(r)} | {fmt(s['overall']['ex'])} | {fmt(s['overall']['ex_strict'])} | "
            f"{fmt(s['overall']['valid_sql_rate'])} | {fmt(s['by_difficulty']['hard']['ex'])} | "
            f"{fmt(s['by_lang']['th']['ex'])} | {fmt(s['by_lang']['en']['ex'])} | "
            f"{fmt(mean([x['latency_s'] for x in rec]), 2)} | {fmt(mean([x['llm_calls'] for x in rec]), 2)} | "
            f"{fmt(mean([x['prompt_tokens'] for x in rec]), 0)} | {fmt(mean([x['completion_tokens'] for x in rec]), 0)} | "
            f"{fmt(recall[r['side']])} | {', '.join(r['llm_errors']) or '–'} | "
            f"{fmt(sum(x.get('retry_wait_s') or 0 for x in rec), 1)} |")
    return lines


def per_question(before: list[dict], after: list[dict]) -> dict:
    """Per question: EX in each run on both sides; better / worse by the number of correct runs."""
    ids = list(before[0]["by_id"])
    rows = {}
    for qid in ids:
        b = [r["by_id"][qid]["ex"] for r in before]
        a = [r["by_id"][qid]["ex"] for r in after]
        change = "better" if sum(a) / len(a) > sum(b) / len(b) else "worse" if sum(a) / len(a) < sum(b) / len(b) else None
        unstable = len(set(b)) > 1 or len(set(a)) > 1
        rows[qid] = {"before": b, "after": a, "change": change, "unstable": unstable}
    return rows


def marks(values: list[bool]) -> str:
    return " ".join("✓" if v else "✗" for v in values)


def sql_cell(run: dict, qid: str) -> str:
    r = run["by_id"][qid]
    sql = (r["pred_sql"] or f"(none: {r['error']})").replace("\n", " ")
    return f"`{sql.replace('|', '\\|').replace('`', "'")}`"


def change_section(config: str, before: list[dict], after: list[dict], rows: dict) -> list[str]:
    items = next(iter(before))["by_id"]
    lines = ["| id | lang | diff | before EX (runs) | after EX (runs) | change | runs disagree |", "|---|---|---|---|---|---|---|"]
    changed = [q for q, v in rows.items() if v["change"] or v["unstable"] or not all(v["before"] + v["after"])]
    for q in changed:
        v = rows[q]
        change = {"better": "wrong→right", "worse": "**right→wrong**", None: "no change"}[v["change"]]
        lines.append(f"| {q} | {items[q]['lang']} | {items[q]['difficulty']} | {marks(v['before'])} | {marks(v['after'])} | "
                     f"{change} | {'yes' if v['unstable'] else ''} |")
    lines += ["", "Questions that are wrong in at least one run, on either side; ✓/✗ = EX of run #1, #2.", ""]
    for kind, title in (("better", "Wrong → right"), ("worse", "Right → wrong"), ("still", "Still wrong after")):
        if kind == "still":
            qs = [q for q in changed if rows[q]["change"] is None and not any(rows[q]["after"])]
        else:
            qs = [q for q in changed if rows[q]["change"] == kind]
        lines += [f"#### {config}: {title} ({len(qs)})", ""]
        if not qs:
            lines += ["(none)", ""]
            continue
        lines += ["| id | run | EX | pred_sql |", "|---|---|---|---|"]
        for q in qs:
            for r in (after if kind == "still" else before + after):
                lines.append(f"| {q} | {label(r)} | {'✓' if r['by_id'][q]['ex'] else '✗'} | {sql_cell(r, q)} |")
        lines.append("")
    return lines


def cost_lines(runs: dict) -> list[str]:
    lines = ["| config | prompt tok before | prompt tok after | Δ per question | Δ % | completion tok before | "
             "completion tok after | llm_calls before | llm_calls after |", "|---|---|---|---|---|---|---|---|---|"]
    for c in CONFIGS:
        avg = {side: {k: mean([x[k] for r in runs[c][side] for x in r["records"]])
                      for k in ("prompt_tokens", "completion_tokens", "llm_calls")} for side in SIDES}
        if avg["before"]["prompt_tokens"] is None or avg["after"]["prompt_tokens"] is None:
            continue
        d = avg["after"]["prompt_tokens"] - avg["before"]["prompt_tokens"]
        lines.append(f"| {c} | {fmt(avg['before']['prompt_tokens'], 0)} | {fmt(avg['after']['prompt_tokens'], 0)} | "
                     f"{d:+.0f} | {d / avg['before']['prompt_tokens']:+.1%} | "
                     f"{fmt(avg['before']['completion_tokens'], 0)} | {fmt(avg['after']['completion_tokens'], 0)} | "
                     f"{fmt(avg['before']['llm_calls'], 2)} | {fmt(avg['after']['llm_calls'], 2)} |")
    return lines


def decide(before: list[dict], after: list[dict], rows: dict) -> tuple[bool, list[str]]:
    ex_b = [r["summary"]["overall"]["ex"] for r in before]
    ex_a = [r["summary"]["overall"]["ex"] for r in after]
    v_b = [r["summary"]["overall"]["valid_sql_rate"] for r in before]
    v_a = [r["summary"]["overall"]["valid_sql_rate"] for r in after]
    better = [q for q, v in rows.items() if v["change"] == "better"]
    worse = [q for q, v in rows.items() if v["change"] == "worse"]
    checks = [
        (min(ex_a) > max(ex_b), f"EX of every after run > every before run: after {ex_a} vs before {ex_b}"),
        (min(v_a) >= max(v_b), f"valid SQL not lower: after {v_a} vs before {v_b}"),
        (len(worse) < len(better), f"right→wrong ({len(worse)}: {', '.join(worse) or '–'}) < "
                                   f"wrong→right ({len(better)}: {', '.join(better) or '–'})"),
    ]
    return all(ok for ok, _ in checks), [f"- {'PASS' if ok else 'FAIL'} — {text}" for ok, text in checks]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Before/after report for pinned rule chunks")
    ap.add_argument("--before", nargs="+", type=Path, required=True, help="results .jsonl of the before runs")
    ap.add_argument("--after", nargs="+", type=Path, required=True, help="results .jsonl of the after runs")
    args = ap.parse_args()

    all_runs = [load_run(p, "before") for p in args.before] + [load_run(p, "after") for p in args.after]
    runs = {c: {side: sorted([r for r in all_runs if r["config"] == c and r["side"] == side],
                             key=lambda r: r["summary"]["timestamp"]) for side in SIDES} for c in CONFIGS}
    # proxy/LLM errors are not model answers: such runs are listed, but scored separately (as in compare.py)
    clean = {c: {side: [r for r in runs[c][side] if not r["llm_errors"]] for side in SIDES} for c in CONFIGS}
    recall_file, recall = recall_numbers()
    rel = lambda p: p.resolve().relative_to(EVAL_DIR.parent).as_posix()  # noqa: E731

    md = ["# Before / after: pin two rule chunks in every prompt", "",
          "Generated by `eval/before_after.py` from the result files listed below; do not edit by hand.", "",
          "## Hypothesis", "",
          f"The rules {', '.join(f'`{r}`' for r in PINNED_RULES)} apply across many questions, but a question's "
          "embedding is often not close to the rule text, so they drop out of the top-k. In the default `full` "
          "runs q12 and q26 failed for that reason (the rule was not in the starting context and the agent did "
          "not call `search_schema` for it). Pinning them next to `relationships` should fix such questions, "
          "at the price of more prompt tokens.", "",
          "## What changed (one variable)", "",
          f"`RetrievalParams.pinned_rules = {PINNED_RULES}` (`eval/run.py --pin-rules`). The pinned chunks are "
          "placed right after `relationships` in every starting prompt, are removed from the ranking so nothing is "
          f"duplicated (`k_schema` = {DEFAULT_RETRIEVAL.k_schema} still counts ranked chunks), and add the DDL of the "
          "tables they name, like a retrieved rule (`rule_tables`). Unchanged: system prompt, k_schema, k_examples "
          f"= {DEFAULT_RETRIEVAL.k_examples}, min_score = {DEFAULT_RETRIEVAL.min_score}, examples, agent loop, "
          "max_steps, stop condition and the `search_schema` tool.", "",
          "## Why these two rules (evidence outside the test set)", "", *examples_evidence(), "",
          "## Reproduce", "", "```bash",
          *[f"uv run --env-file .env eval/run.py --config {c} --{'' if side == 'after' else 'no-'}pin-rules"
            f"   # {side}, run twice" for side in SIDES for c in CONFIGS],
          "uv run --env-file .env eval/retrieval_eval.py",
          "uv run eval/before_after.py --before " + " ".join(rel(r["path"]) for c in CONFIGS for r in runs[c]["before"])
          + " --after " + " ".join(rel(r["path"]) for c in CONFIGS for r in runs[c]["after"]),
          "```", "",
          "## Every run", "",
          f"Schema recall@{RECALL_K} at min_score {RECALL_MIN_SCORE}: share of gold tables whose DDL is in the "
          f"starting prompt (no chat model; `{recall_file}`; the same for every run on a side because retrieval "
          "is deterministic; for `full` the agent may still add tables via `search_schema`).", "",
          *run_table([r for c in CONFIGS for side in SIDES for r in runs[c][side]], recall), ""]
    errored = [r for c in CONFIGS for side in SIDES for r in runs[c][side] if r["llm_errors"]]
    if errored:
        md += ["Runs with proxy/LLM errors are kept and listed above but left out of the comparison below: "
               + "; ".join(f"{label(r)} on {', '.join(r['llm_errors'])}" for r in errored), ""]

    md += ["## Per question", ""]
    verdict = None
    for c in CONFIGS:
        b, a = clean[c]["before"], clean[c]["after"]
        if not b or not a:
            md += [f"### {c}", "", "(missing runs)", ""]
            continue
        rows = per_question(b, a)
        md += [f"### {c}", "", *change_section(c, b, a, rows)]
        if c == "full":
            verdict = decide(b, a, rows)

    md += ["## Cost", "", "Averages over all runs of a side (per question).", "", *cost_lines(clean), ""]
    md += ["## Decision", "",
           "Rule fixed in CLAUDE.md before the after runs (its reading below — every run, per-question count of "
           "correct runs — was written into this script before the after runs too): switch the default on only if, for `full`, EX is higher in every "
           "after run than in every before run, valid SQL rate does not drop, and fewer questions go right→wrong "
           "than wrong→right.", ""]
    if verdict is None:
        md += ["(not decided: missing full runs)", ""]
    else:
        ok, lines = verdict
        md += [*lines, "", f"**Result: {'PASS — switch the default on' if ok else 'FAIL — keep the default off'}.**", ""]

    md += ["## Limitations", "",
           "- 30 questions: one question = 3.3 percentage points of EX; two runs per side.",
           "- The two pinned rules were picked from the error analysis of the same test set (there is no dev "
           "split), so the after numbers are optimistic for unseen questions; the examples count above is the only "
           "evidence from outside the test set.",
           "- The course proxy is not deterministic at temperature 0: the same prompt can give different SQL, so "
           "a question that flips in only one run may be noise.",
           "- Recall is measured on the starting prompt only; it says nothing about whether the model uses the rule.",
           "",
           "## Not done here (proposals)", "",
           "- q25: AND/OR precedence in the WHERE clause (prompt guidance or an example), a separate experiment.",
           "- q26/q28: English questions answered with `name_th`; tell the model to use `name_en` for English "
           "questions (system prompt change, a separate experiment).",
           "- Agent stop condition: an exploratory query can end up as the final answer; requiring an explicit "
           "final SQL would need its own before/after.",
           "- A dev split (or new hard questions) to choose pinned rules without using the test set — needs the "
           "owner's decision, the test set is locked.", ""]
    OUT.write_text("\n".join(md), encoding="utf-8", newline="\n")
    print(f"-> {OUT}")
    if verdict:
        print("\n".join(verdict[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
