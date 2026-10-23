"""Model and runtime settings. Credentials come only from OPENAI_API_KEY / OPENAI_BASE_URL in the environment."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
SCHEMA_DOCS = DATA_DIR / "schema_docs.md"
EXAMPLES = DATA_DIR / "examples.jsonl"
INDEX_PATH = DATA_DIR / "index.json"  # built by scripts/build_index.py, not committed

TABLES = ("orders", "order_items", "products", "categories", "customers", "payments", "reviews", "coupons")

MODEL = "gemma-4-E4B-it"
EMBED_MODEL = "bge-m3"
EMBED_BATCH = 32
TEMPERATURE = 0.0
MAX_TOKENS = 1024
QUERY_TIMEOUT_S = 10.0
