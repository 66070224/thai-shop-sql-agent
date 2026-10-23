"""Model and runtime settings. Credentials come only from OPENAI_API_KEY / OPENAI_BASE_URL in the environment."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
SCHEMA_DOCS = DATA_DIR / "schema_docs.md"

MODEL = "gemma-4-E4B-it"
TEMPERATURE = 0.0
MAX_TOKENS = 1024
QUERY_TIMEOUT_S = 10.0
