"""Validate eval/testset.jsonl: field schema, lang/difficulty distribution, and that every gold SQL runs non-empty."""

import json
import re
import sys
from collections import Counter
from pathlib import Path

from thai_shop_agent.db import connect_ro, run_query

TESTSET = Path(__file__).resolve().parent / "testset.jsonl"

FIELDS = {"id": str, "question": str, "lang": str, "difficulty": str, "gold_sql": str, "tables": list,
          "traps": list, "order_matters": bool, "notes": str}
LANGS = {"th": 15, "en": 15}
DIFFICULTIES = {"easy": 10, "medium": 12, "hard": 8}
TRAPS = {"cancelled_refunded", "null_dates", "duplicate_customers", "province_aliases", "multi_payment"}
TABLES = {"categories", "products", "customers", "coupons", "orders", "order_items", "payments", "reviews"}


def load(path: Path = TESTSET) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def check_item(item: dict) -> list[str]:
    errs = []
    if set(item) != set(FIELDS):
        errs.append(f"fields {sorted(item)} != {sorted(FIELDS)}")
        return errs
    for key, typ in FIELDS.items():
        if not isinstance(item[key], typ):
            errs.append(f"{key} should be {typ.__name__}")
    if item["lang"] not in LANGS:
        errs.append(f"bad lang {item['lang']!r}")
    if item["difficulty"] not in DIFFICULTIES:
        errs.append(f"bad difficulty {item['difficulty']!r}")
    if bad := set(item["traps"]) - TRAPS:
        errs.append(f"unknown traps {sorted(bad)}")
    if bad := set(item["tables"]) - TABLES:
        errs.append(f"unknown tables {sorted(bad)}")
    sql_tables = {t for t in TABLES if re.search(rf"\b{t}\b", item["gold_sql"], re.IGNORECASE)}
    if sql_tables != set(item["tables"]):
        errs.append(f"tables {sorted(item['tables'])} != tables in gold_sql {sorted(sql_tables)}")
    if item["order_matters"] and "ORDER BY" not in item["gold_sql"].upper():
        errs.append("order_matters but gold_sql has no ORDER BY")
    if item["difficulty"] == "easy" and (item["traps"] or len(item["tables"]) != 1):
        errs.append("easy must be 1 table with no traps")
    if item["lang"] == "th" and not re.search(r"[฀-๿]", item["question"]):
        errs.append("lang th but question has no Thai text")
    return errs


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    items = load()
    problems = []
    ids = Counter(i.get("id") for i in items)
    problems += [f"duplicate id {k}" for k, n in ids.items() if n > 1]
    questions = Counter(i.get("question") for i in items)
    problems += [f"duplicate question {k!r}" for k, n in questions.items() if n > 1]

    conn = connect_ro()
    for item in items:
        errs = check_item(item)
        if not errs:
            try:
                cols, rows = run_query(conn, item["gold_sql"])
                if not rows or all(v is None for r in rows for v in r):
                    errs.append("gold SQL returned no rows / only NULL")
                else:
                    print(f"{item['id']}  {len(rows):>3} rows  {cols}")
            except Exception as e:  # noqa: BLE001 - report any SQL failure
                errs.append(f"gold SQL failed: {e}")
        problems += [f"{item.get('id')}: {e}" for e in errs]

    for key, want in (("lang", LANGS), ("difficulty", DIFFICULTIES)):
        got = Counter(i.get(key) for i in items)
        if dict(got) != want:
            problems.append(f"{key} distribution {dict(got)} != {want}")
    for diff in DIFFICULTIES:
        langs = {i["lang"] for i in items if i.get("difficulty") == diff}
        if langs != set(LANGS):
            problems.append(f"difficulty {diff} has langs {sorted(langs)}, need both")

    print(f"\n{len(items)} items; lang {dict(Counter(i['lang'] for i in items))}; "
          f"difficulty {dict(Counter(i['difficulty'] for i in items))}")
    if problems:
        print("\nFAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
