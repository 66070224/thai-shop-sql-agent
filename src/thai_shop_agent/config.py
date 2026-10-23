"""Model and runtime settings. Credentials come only from OPENAI_API_KEY / OPENAI_BASE_URL in the environment."""

from dataclasses import dataclass
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
MAX_STEPS = 5  # agent: model calls per question


@dataclass(frozen=True)
class RetrievalParams:
    """How much retrieved context goes into the prompt. No defaults yet: they are chosen in eval/sweep.py."""

    k_schema: int  # table + rule chunks (relationships is always added)
    k_examples: int  # question -> SQL examples; 0 = zero-shot
    min_score: float  # cosine threshold for both
    rule_tables: bool = True  # also add DDL of tables named by retrieved rules
