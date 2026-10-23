"""Pydantic models at the agent's boundaries: tool arguments in, the final answer out."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_SEARCH_K = 8


class SearchSchemaArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500, description="what to look up: tables, columns, or a business rule")
    k: int = Field(default=3, ge=1, le=MAX_SEARCH_K, description="how many schema sections to return")


class RunSqlArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=5000, description="one SQLite SELECT statement")


class Citations(BaseModel):
    tables: list[str]  # tables the final SQL reads (sqlglot)
    chunks: list[str]  # schema chunk / example ids that were placed in the context


class Usage(BaseModel):
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


StopReason = Literal["answered", "max_steps", "no_progress"]


class FinalAnswer(BaseModel):
    answer: str
    sql: str | None  # last SQL that ran successfully (as executed, LIMIT included); None if none did
    columns: list[str] = []
    rows: list[list[Any]] = []
    citations: Citations
    steps: int  # tool calls executed
    stopped_reason: StopReason
    usage: Usage
