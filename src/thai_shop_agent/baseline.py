"""Single-shot baselines: one chat call, no tools, no retry.

B0  full DDL + full schema docs + question, zero-shot
B1  retrieved context (relationships + top-k tables/rules + their DDL) + question, zero-shot
B2  B1 + top-k similar examples (question -> SQL)
B1 and B2 use the same system prompt and output instruction as B0; only the context changes.
"""

import sqlite3
from dataclasses import dataclass, field

from thai_shop_agent.config import SCHEMA_DOCS, RetrievalParams
from thai_shop_agent.db import schema_ddl, table_ddl
from thai_shop_agent.llm import LLM
from thai_shop_agent.retrieval import Retriever, build_prompt_context
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
    context_ids: list[str] = field(default_factory=list)  # retrieved chunk ids placed in the prompt


def build_b0_messages(ddl: str, docs: str, question: str) -> list[dict]:
    user = (
        f"## Database schema (SQLite DDL)\n\n{ddl}\n\n"
        f"## Schema documentation and business rules\n\n{docs}\n\n"
        f"## Question\n\n{question}\n\n"
        "Write the SQL query."
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def build_retrieval_messages(context: str, question: str) -> list[dict]:
    user = f"{context}\n\n## Question\n\n{question}\n\nWrite the SQL query."
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


class RetrievalBaseline:
    """B1 when params.k_examples == 0, B2 otherwise."""

    def __init__(self, llm: LLM, conn: sqlite3.Connection, retriever: Retriever, params: RetrievalParams):
        self.llm = llm
        self.ddl = table_ddl(conn)
        self.retriever = retriever
        self.params = params

    def predict(self, question: str) -> Prediction:
        p = self.params
        ctx = build_prompt_context(self.retriever, question, p.k_schema, p.k_examples, p.min_score, self.ddl,
                                   p.rule_tables)
        out = self.llm.chat(build_retrieval_messages(ctx.text, question))
        return Prediction(sql=strip_code_fence(out.text), raw_output=out.text,
                          prompt_tokens=out.prompt_tokens, completion_tokens=out.completion_tokens,
                          context_ids=ctx.chunk_ids)
