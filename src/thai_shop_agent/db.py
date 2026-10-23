"""Read-only SQLite access with a per-query timeout."""

import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "shop.db"


class QueryTimeout(Exception):
    pass


def connect_ro(db_path: Path = DB_PATH) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} not found; run scripts/generate_data.py and scripts/build_db.py")
    return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)


def run_query(conn: sqlite3.Connection, sql: str, timeout_s: float = 10.0) -> tuple[list[str], list[tuple]]:
    """Execute one statement; return (column names, rows). Raises QueryTimeout or sqlite3.Error."""
    deadline = time.monotonic() + timeout_s
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
    except sqlite3.OperationalError as e:
        if time.monotonic() > deadline and "interrupted" in str(e):
            raise QueryTimeout(f"query exceeded {timeout_s}s") from e
        raise
    finally:
        conn.set_progress_handler(None, 0)
    cols = [d[0] for d in cur.description] if cur.description else []
    return cols, rows


def table_ddl(conn: sqlite3.Connection) -> dict[str, str]:
    """{table name: CREATE TABLE statement} from sqlite_master (indexes left out), in creation order."""
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL ORDER BY rowid")
    return {name: sql.strip() + ";" for name, sql in rows}


def schema_ddl(conn: sqlite3.Connection) -> str:
    """CREATE TABLE statements for every table."""
    return "\n\n".join(table_ddl(conn).values())
