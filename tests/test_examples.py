import sys
from pathlib import Path

import pytest

from thai_shop_agent.db import DB_PATH

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import validate_examples  # noqa: E402

needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="build data/shop.db first")


@pytest.mark.parametrize("a, b", [
    ("How many customers?", "how many   customers"),
    ("ยอดขาย ปี 2025?", "ยอดขาย ปี 2025"),
])
def test_norm_question(a, b):
    assert validate_examples.norm_question(a) == validate_examples.norm_question(b)


def test_norm_sql_ignores_case_and_whitespace():
    assert validate_examples.norm_sql("select  COUNT(*) from Orders;") == validate_examples.norm_sql(
        "SELECT count(*)\nFROM orders")


def test_leak_flags_paraphrase_and_same_sql():
    test = {"question": "How many customers do we have in Bangkok?", "gold_sql": "SELECT 1", "order_matters": False}
    item = {"question": "How many customers do we have in Bangkok", "sql": "select 1"}
    why = validate_examples.leaks(item, ([ "x"], [(1,)]), test, (["y"], [(1,)]))
    assert why == ["question ratio 1.00", "same normalized SQL", "result matches gold (EX)"]


@needs_db
def test_example_bank_is_valid_and_does_not_leak():
    assert validate_examples.main() == 0
