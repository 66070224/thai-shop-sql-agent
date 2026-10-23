"""B0 baseline: full DDL + full schema docs + question, zero-shot, one call, no tools, no retry."""

import sqlite3
from dataclasses import dataclass

from thai_shop_agent.config import SCHEMA_DOCS
from thai_shop_agent.db import schema_ddl
from thai_shop_agent.llm import LLM
from thai_shop_agent.sql_guard import strip_code_fence

SYSTEM_PROMPT = (
    "You are an expert data analyst who writes SQLite queries for a Thai online shop. "
    "Questions may be in Thai or English. Follow the business rules in the schema documentation. "
    "Answer with a single SQLite SELECT statement only: no explanation, no comments."
)


@dataclass
class Prediction:
    sql: str
    raw_output: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    llm_calls: int = 1


def build_b0_messages(ddl: str, docs: str, question: str) -> list[dict]:
    user = (
        f"## Database schema (SQLite DDL)\n\n{ddl}\n\n"
        f"## Schema documentation and business rules\n\n{docs}\n\n"
        f"## Question\n\n{question}\n\n"
        "Write the SQL query."
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


class ZeroShotBaseline:
    def __init__(self, llm: LLM, conn: sqlite3.Connection):
        self.llm = llm
        self.ddl = schema_ddl(conn)
        self.docs = SCHEMA_DOCS.read_text(encoding="utf-8")

    def predict(self, question: str) -> Prediction:
        out = self.llm.chat(build_b0_messages(self.ddl, self.docs, question))
        return Prediction(sql=strip_code_fence(out.text), raw_output=out.text,
                          prompt_tokens=out.prompt_tokens, completion_tokens=out.completion_tokens)
