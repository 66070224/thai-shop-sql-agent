"""Validate the few-shot bank data/examples.jsonl and check it does not leak the test set.

    uv run eval/validate_examples.py

Per example: field schema, tables == tables the SQL reads (sqlglot), SQL passes sql_guard,
runs, and returns rows that are not all NULL. Coverage: both languages about evenly, every
difficulty and every trap at least once.

Leak check, every example against every test question:
  - question: normalized texts with difflib ratio >= 0.8
  - SQL: normalized example SQL identical to normalized gold SQL
  - result: the example's result would score EX against that question's gold result
    (catches a paraphrased test question whose SQL differs only in form)
Flagged pairs are printed; fix the example, never the test set.
"""

import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import sqlglot

from thai_shop_agent.config import DATA_DIR
from thai_shop_agent.db import connect_ro, run_query
from thai_shop_agent.eval_metrics import ex_match
from thai_shop_agent.sql_guard import GuardError, prepare, referenced_tables
from validate_testset import DIFFICULTIES, LANGS, TABLES, TRAPS
from validate_testset import load as load_testset

EXAMPLES = DATA_DIR / "examples.jsonl"
FIELDS = {"id": str, "question": str, "lang": str, "difficulty": str, "sql": str, "tables": list, "traps": list}
QUESTION_RATIO = 0.8
MAX_LANG_GAP = 2


def load(path: Path = EXAMPLES) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def norm_question(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace (Thai characters are kept)."""
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(text.split())


def norm_sql(sql: str) -> str:
    """Canonical sqlglot rendering: same query modulo whitespace, case, and quoting."""
    try:
        return sqlglot.parse_one(sql.strip().rstrip(";"), read="sqlite").sql(dialect="sqlite", normalize=True)
    except sqlglot.errors.SqlglotError:
        return " ".join(sql.lower().split())


def check_item(item: dict) -> list[str]:
    if set(item) != set(FIELDS):
        return [f"fields {sorted(item)} != {sorted(FIELDS)}"]
    errs = [f"{k} should be {t.__name__}" for k, t in FIELDS.items() if not isinstance(item[k], t)]
    if errs:
        return errs
    if item["lang"] not in LANGS:
        errs.append(f"bad lang {item['lang']!r}")
    if item["difficulty"] not in DIFFICULTIES:
        errs.append(f"bad difficulty {item['difficulty']!r}")
    if bad := set(item["traps"]) - TRAPS:
        errs.append(f"unknown traps {sorted(bad)}")
    if bad := set(item["tables"]) - TABLES:
        errs.append(f"unknown tables {sorted(bad)}")
    if item["difficulty"] == "easy" and (item["traps"] or len(item["tables"]) != 1):
        errs.append("easy must be 1 table with no traps")
    if item["lang"] == "th" and not re.search(r"[฀-๿]", item["question"]):
        errs.append("lang th but question has no Thai text")
    try:
        prepare(item["sql"])
    except GuardError as e:
        errs.append(f"SQL rejected by guard: {e}")
        return errs
    if (used := referenced_tables(item["sql"])) != sorted(item["tables"]):
        errs.append(f"tables {sorted(item['tables'])} != tables in sql {used}")
    return errs


def leaks(item: dict, result: tuple[list, list] | None, test: dict, gold: tuple[list, list]) -> list[str]:
    found = []
    ratio = difflib.SequenceMatcher(None, norm_question(item["question"]), norm_question(test["question"])).ratio()
    if ratio >= QUESTION_RATIO:
        found.append(f"question ratio {ratio:.2f}")
    if norm_sql(item["sql"]) == norm_sql(test["gold_sql"]):
        found.append("same normalized SQL")
    if result is not None:
        (gold_cols, gold_rows), (cols, rows) = gold, result
        if ex_match(gold_rows, rows, len(gold_cols), len(cols), test["order_matters"]):
            found.append("result matches gold (EX)")
    return found


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    items, tests = load(), load_testset()
    problems = []
    for key in ("id", "question"):
        problems += [f"duplicate {key} {k!r}" for k, n in Counter(i.get(key) for i in items).items() if n > 1]

    conn = connect_ro()
    gold = {t["id"]: run_query(conn, t["gold_sql"]) for t in tests}
    results: dict[str, tuple[list, list]] = {}
    for item in items:
        errs = check_item(item)
        if not errs:
            try:
                cols, rows = run_query(conn, prepare(item["sql"]))
            except Exception as e:  # noqa: BLE001 - report any SQL failure
                errs.append(f"SQL failed: {e}")
            else:
                if not rows or all(v is None for r in rows for v in r):
                    errs.append("SQL returned no rows / only NULL")
                else:
                    results[item["id"]] = (cols, rows)
                    print(f"{item['id']}  {item['lang']} {item['difficulty']:<6} {len(rows):>3} rows  {cols}")
        problems += [f"{item.get('id')}: {e}" for e in errs]

    flagged = []
    for item in items:
        for test in tests:
            if why := leaks(item, results.get(item["id"]), test, gold[test["id"]]):
                flagged.append((item, test, why))

    langs = Counter(i.get("lang") for i in items)
    if abs(langs["th"] - langs["en"]) > MAX_LANG_GAP:
        problems.append(f"lang split {dict(langs)} is off by more than {MAX_LANG_GAP}")
    diffs = Counter(i.get("difficulty") for i in items)
    problems += [f"no {d} example" for d in DIFFICULTIES if not diffs[d]]
    traps = Counter(t for i in items for t in i.get("traps", []))
    problems += [f"no example covers trap {t}" for t in sorted(TRAPS) if not traps[t]]

    print(f"\n{len(items)} examples; lang {dict(langs)}; difficulty {dict(diffs)}")
    print(f"traps {dict(sorted(traps.items()))}")
    if flagged:
        print(f"\nLEAK CHECK: {len(flagged)} flagged pair(s) (fix the example, not the test set):")
        for item, test, why in flagged:
            print(f"  {item['id']} ~ {test['id']}: {', '.join(why)}")
            print(f"    example: {item['question']}")
            print(f"    test   : {test['question']}")
        problems.append(f"{len(flagged)} leak flag(s)")
    else:
        print("\nLEAK CHECK: no example is close to a test question")
    if problems:
        print("\nFAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
