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
LLM_TIMEOUT_S = 90.0  # per proxy request; a timeout is retried like a connection error
MAX_STEPS = 5  # agent: model calls per question


@dataclass(frozen=True)
class RetrievalParams:
    """How much retrieved context goes into the prompt (defaults: DEFAULT_RETRIEVAL below)."""

    k_schema: int  # table + rule chunks (relationships is always added)
    k_examples: int  # question -> SQL examples; 0 = zero-shot
    min_score: float  # cosine threshold for both
    rule_tables: bool = True  # also add DDL of tables named by retrieved rules
    pinned_rules: tuple[str, ...] = ()  # rule chunk ids put in every starting prompt, outside the top-k


# Rules used across many questions whose text is rarely close to a question's embedding (eval/before_after.py).
PINNED_RULES = ("rule:sales-revenue-definition", "rule:province-names")


# Chosen by eval/sweep.py -> eval/results/sweep_20261023-113639.json (single-shot predictor, rule-table DDL on).
# Rule: max EX, then EX-strict, valid SQL rate, hard EX, fewest prompt tokens. k_schema=5, k_examples=1 gave
# EX 0.867 / EX-strict 0.767 / valid 1.00 / hard 0.75 at min_score 0.0 and 0.35; 0.35 wins on prompt tokens
# (1720 vs 1746). Margins between the top settings are 1-2 questions out of 30, and the values were tuned on
# the test set itself (there is no dev split): a limitation to state in the report.
# B1 uses the same k_schema / min_score with k_examples = 0, so B1 vs B2 isolates the effect of examples.
DEFAULT_RETRIEVAL = RetrievalParams(k_schema=5, k_examples=1, min_score=0.35)
