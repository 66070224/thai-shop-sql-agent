import importlib.util
from pathlib import Path

import pytest

from thai_shop_agent.db import DB_PATH, connect_ro, run_query
from thai_shop_agent.sql_guard import GuardError, add_limit, clean, guard, prepare, referenced_tables

needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="build data/shop.db first")

RUN_PY = Path(__file__).resolve().parent.parent / "eval" / "run.py"
spec = importlib.util.spec_from_file_location("eval_run_guard", RUN_PY)
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


@pytest.mark.parametrize("raw, sql", [
    ("```sql\nSELECT 1;\n```", "SELECT 1"),
    ("SELECT 1 ;; \n", "SELECT 1"),
    ("  SELECT 2  ", "SELECT 2"),
])
def test_clean(raw, sql):
    assert clean(raw) == sql


@pytest.mark.parametrize("sql, message", [
    ("", "empty"),
    ("SELECT * FROM orders; DROP TABLE orders", "one SQL statement"),
    ("SELECT 1; SELECT 2", "one SQL statement"),
    ("SELECT 1 /* a */ ; DELETE FROM orders", "one SQL statement"),
    ("SELECT 1 -- harmless\n; DROP TABLE orders", "one SQL statement"),
    ("SELECT 1 /* /* */ ; DROP TABLE orders; /* */", "one SQL statement"),  # SQLite has no nested comments
    ("-- SELECT\nDELETE FROM orders", "must start with SELECT or WITH"),
    ("/* SELECT */ DROP TABLE orders", "must start with SELECT or WITH"),
    ("DROP TABLE orders", "must start with SELECT or WITH"),
    ("ATTACH DATABASE 'evil.db' AS evil", "must start with SELECT or WITH"),
    ("PRAGMA table_info(orders)", "must start with SELECT or WITH"),
    ("VACUUM", "must start with SELECT or WITH"),
    ("REPLACE INTO orders (id) VALUES (1)", "must start with SELECT or WITH"),
    ("WITH x AS (SELECT 1) DELETE FROM orders", "DELETE is not allowed"),
    ("WITH x AS (SELECT 1) INSERT INTO orders SELECT * FROM x", "INSERT is not allowed"),
    ("SELECT * FROM orders WHERE id IN (SELECT 1) AND 1 = (SELECT 1 FROM (SELECT 1) UNION SELECT 2) "
     "OR EXISTS (SELECT 1) ORDER BY 1 -- pragma\n", None),  # comment words are not keywords
    ("SELECT name FROM sqlite_master", "'sqlite_master' does not exist or is not allowed"),
    ("SELECT * FROM sqlite_schema", "not allowed"),
    ("SELECT * FROM users", "'users' does not exist or is not allowed"),
    ("SELECT * FROM orders o JOIN secrets s ON s.id = o.id", "'secrets'"),
    ("SELECT * FROM orders WHERE id IN (SELECT id FROM temp_x)", "'temp_x'"),
    ("SELECT * FROM evil.orders", "evil.orders is not allowed"),
    ("SELECT * FROM pragma_table_info('orders')", "table-valued functions"),
    ("SELECT * FROM json_each('[1,2]')", "table-valued functions"),
    ("SELECT FROM WHERE", "syntax error"),
])
def test_guard_rejects(sql, message):
    if message is None:
        assert guard(sql) == sql
        return
    with pytest.raises(GuardError, match=message.replace("(", r"\(").replace(")", r"\)")):
        guard(sql)


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM orders",
    "select status, count(*) from orders group by status",
    "WITH paid AS (SELECT * FROM orders WHERE status NOT IN ('cancelled', 'refunded')) "
    "SELECT COUNT(*) FROM paid",
    "WITH a AS (SELECT customer_id FROM orders), b AS (SELECT * FROM a) SELECT * FROM b JOIN customers c ON c.id = b.customer_id",
    "SELECT name FROM products WHERE category_id IN (SELECT id FROM categories WHERE name_en = 'Books')",
    "SELECT * FROM (SELECT id, price FROM products) AS p WHERE p.price > 100",
    "SELECT customer_id, SUM(amount), RANK() OVER (ORDER BY SUM(amount) DESC) AS rnk FROM payments GROUP BY customer_id",
    "SELECT REPLACE(province, ' ', '') FROM customers",
    "SELECT 'drop table orders; delete' AS note FROM orders",
    'SELECT "update" FROM orders',
    "SELECT * FROM main.orders",
    "SELECT o.id FROM orders o UNION SELECT r.order_id FROM reviews r",
])
def test_guard_allows(sql):
    assert guard(sql) == sql


@pytest.mark.parametrize("sql, expected", [
    ("SELECT * FROM orders", "SELECT * FROM orders\nLIMIT 100"),
    ("SELECT * FROM orders LIMIT 5", "SELECT * FROM orders LIMIT 5"),
    ("SELECT * FROM orders ORDER BY id LIMIT 5 OFFSET 10", "SELECT * FROM orders ORDER BY id LIMIT 5 OFFSET 10"),
    ("SELECT * FROM (SELECT * FROM orders LIMIT 5)", "SELECT * FROM (SELECT * FROM orders LIMIT 5)\nLIMIT 100"),
    ("WITH x AS (SELECT * FROM orders LIMIT 3) SELECT * FROM x", "WITH x AS (SELECT * FROM orders LIMIT 3) SELECT * FROM x\nLIMIT 100"),
    ("SELECT id FROM orders UNION SELECT id FROM products", "SELECT id FROM orders UNION SELECT id FROM products\nLIMIT 100"),
    ("SELECT id FROM orders UNION SELECT id FROM products LIMIT 7", "SELECT id FROM orders UNION SELECT id FROM products LIMIT 7"),
    ("SELECT * FROM orders -- all", "SELECT * FROM orders -- all\nLIMIT 100"),
])
def test_add_limit(sql, expected):
    assert add_limit(sql) == expected


def test_prepare_chains_all_steps():
    assert prepare("```sql\nSELECT id FROM orders;\n```") == "SELECT id FROM orders\nLIMIT 100"
    with pytest.raises(GuardError):
        prepare("```sql\nDROP TABLE orders;\n```")


def test_referenced_tables_excludes_ctes():
    sql = "WITH x AS (SELECT * FROM orders) SELECT * FROM x JOIN customers c ON c.id = x.customer_id"
    assert referenced_tables(sql) == ["customers", "orders"]


@needs_db
def test_limit_runs_on_sqlite():
    cols, rows = run_query(connect_ro(), prepare("SELECT order_id FROM orders UNION SELECT product_id FROM products"))
    assert len(rows) == 100


@needs_db
@pytest.mark.parametrize("item", run.load_testset(), ids=lambda i: i["id"])
def test_gold_sql_passes_guard_within_limit(item):
    conn = connect_ro()
    sql = prepare(item["gold_sql"])
    _, guarded_rows = run_query(conn, sql)
    _, raw_rows = run_query(conn, item["gold_sql"])
    assert len(raw_rows) <= 100
    assert guarded_rows == raw_rows
