import importlib.util
import sqlite3
from pathlib import Path

import pytest

from thai_shop_agent.baseline import Prediction, build_b0_messages
from thai_shop_agent.db import DB_PATH, QueryTimeout, connect_ro, run_query
from thai_shop_agent.sql_guard import strip_code_fence

RUN_PY = Path(__file__).resolve().parent.parent / "eval" / "run.py"
spec = importlib.util.spec_from_file_location("eval_run", RUN_PY)
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="build data/shop.db first")


class FixedPredictor:
    def __init__(self, sql_for):
        self.sql_for = sql_for

    def predict(self, question):
        sql = self.sql_for(question)
        return Prediction(sql=sql, raw_output=sql)


@pytest.mark.parametrize("raw, sql", [
    ("```sql\nSELECT 1;\n```", "SELECT 1;"),
    ("Here you go:\n```SQL\nSELECT a\nFROM t\n```\nDone.", "SELECT a\nFROM t"),
    ("```\nSELECT 2\n```", "SELECT 2"),
    ("  SELECT 3  ", "SELECT 3"),
    ("```sql\nSELECT 4", "SELECT 4"),
    ("```sqlite\nSELECT 5\n```", "SELECT 5"),
    ("```sqlite\nSELECT 6", "SELECT 6"),
])
def test_strip_code_fence(raw, sql):
    assert strip_code_fence(raw) == sql


def test_b0_prompt_contains_schema_docs_and_question():
    msgs = build_b0_messages("CREATE TABLE t (a INTEGER);", "## Rule: x", "ยอดขายเท่าไหร่")
    user = msgs[-1]["content"]
    assert "CREATE TABLE t" in user and "## Rule: x" in user and "ยอดขายเท่าไหร่" in user


@needs_db
def test_gold_sql_scores_perfectly():
    items = run.load_testset()
    gold = {i["question"]: i["gold_sql"] for i in items}
    conn = connect_ro()
    records = [run.evaluate_item(i, FixedPredictor(gold.get), conn) for i in items]
    summary = run.summarize(records)
    assert summary["overall"] == {**summary["overall"], "n": 30, "ex": 1.0, "ex_strict": 1.0, "valid_sql_rate": 1.0}


@needs_db
def test_invalid_and_wrong_sql_are_scored():
    item = next(i for i in run.load_testset() if i["id"] == "q13")
    conn = connect_ro()
    bad = run.evaluate_item(item, FixedPredictor(lambda q: "SELEC nonsense"), conn)
    assert (bad["ok"], bad["ex"], bad["error"] is not None) == (False, False, True)
    naive = "SELECT SUM(quantity * unit_price - discount) FROM order_items"  # forgets cancelled/refunded and year
    wrong = run.evaluate_item(item, FixedPredictor(lambda q: naive), conn)
    assert (wrong["ok"], wrong["ex"], wrong["ex_strict"]) == (True, False, False)


@needs_db
def test_read_only_connection_rejects_writes():
    conn = connect_ro()
    with pytest.raises(Exception, match="readonly"):
        conn.execute("DELETE FROM categories")


@needs_db
def test_llm_failure_is_recorded_not_raised():
    class Boom:
        def predict(self, question):
            raise RuntimeError("proxy down")
    item = run.load_testset()[0]
    rec = run.evaluate_item(item, Boom(), connect_ro())
    assert rec["ok"] is False and rec["error"].startswith("llm_error")


def test_query_timeout():
    conn = sqlite3.connect(":memory:")
    slow = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n"
    with pytest.raises(QueryTimeout):
        run_query(conn, slow, timeout_s=0.2)
    assert run_query(conn, "SELECT 1")[1] == [(1,)]  # connection still usable
