"""Load the generated CSVs into data/shop.db (SQLite) with types and foreign keys.

Empty CSV fields become NULL. The database is rebuilt from scratch every run.
"""

import argparse
import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DDL = """
CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    name_th     TEXT NOT NULL,
    name_en     TEXT NOT NULL
) STRICT;

CREATE TABLE products (
    product_id  INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    category_id INTEGER REFERENCES categories(category_id),
    price       REAL NOT NULL,
    cost        REAL NOT NULL
) STRICT;

CREATE TABLE customers (
    customer_id   INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL,
    phone         TEXT NOT NULL,
    province      TEXT NOT NULL,
    signup_date   TEXT NOT NULL,
    member_status TEXT NOT NULL CHECK (member_status IN ('none', 'silver', 'gold'))
) STRICT;

CREATE TABLE coupons (
    coupon_id      INTEGER PRIMARY KEY,
    code           TEXT NOT NULL UNIQUE,
    discount_type  TEXT NOT NULL CHECK (discount_type IN ('percent', 'fixed')),
    discount_value REAL NOT NULL,
    valid_from     TEXT NOT NULL,
    valid_to       TEXT NOT NULL
) STRICT;

CREATE TABLE orders (
    order_id     INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(customer_id),
    status       TEXT NOT NULL
                 CHECK (status IN ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'refunded')),
    channel      TEXT NOT NULL CHECK (channel IN ('app', 'web', 'line', 'facebook')),
    coupon_id    INTEGER REFERENCES coupons(coupon_id),
    created_at   TEXT NOT NULL,
    paid_at      TEXT,
    shipped_at   TEXT,
    delivered_at TEXT
) STRICT;

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders(order_id),
    product_id    INTEGER NOT NULL REFERENCES products(product_id),
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    unit_price    REAL NOT NULL,
    discount      REAL NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE payments (
    payment_id INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(order_id),
    method     TEXT NOT NULL CHECK (method IN ('PromptPay', 'card', 'transfer', 'COD')),
    amount     REAL NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('success', 'failed', 'refunded')),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE reviews (
    review_id  INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(order_id),
    rating     INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment    TEXT,
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX idx_products_category ON products(category_id);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_created ON orders(created_at);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_order_items_product ON order_items(product_id);
CREATE INDEX idx_payments_order ON payments(order_id);
CREATE INDEX idx_reviews_order ON reviews(order_id);
"""

# Load order respects foreign keys.
TABLES = ["categories", "products", "customers", "coupons", "orders", "order_items", "payments", "reviews"]


def column_types(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    return [(row[1], row[2]) for row in conn.execute(f"PRAGMA table_info({table})")]


def convert(value: str, sql_type: str) -> int | float | str | None:
    if value == "":
        return None
    if sql_type == "INTEGER":
        return int(value)
    if sql_type == "REAL":
        return float(value)
    return value


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv-dir", type=Path, default=ROOT / "data" / "csv")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "shop.db")
    args = ap.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.db.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(DDL)
    for table in TABLES:
        cols = column_types(conn, table)
        with (args.csv_dir / f"{table}.csv").open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
            if header != [c for c, _ in cols]:
                raise SystemExit(f"{table}.csv header {header} does not match schema {[c for c, _ in cols]}")
            rows = [tuple(convert(v, t) for v, (_, t) in zip(row, cols)) for row in reader]
        placeholders = ", ".join("?" * len(cols))
        with conn:
            conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        print(f"{table:<12} {len(rows):>6} rows")
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise SystemExit(f"foreign key violations: {bad[:5]}")
    conn.execute("VACUUM")
    conn.close()
    tmp.replace(args.db)
    print(f"-> {args.db}")


if __name__ == "__main__":
    main()
