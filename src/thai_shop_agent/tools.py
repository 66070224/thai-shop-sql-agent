"""The agent's two tools and their dispatcher.

search_schema(query, k)  schema-doc sections (tables + rules) with the DDL they need
run_sql(sql)             sql_guard.prepare -> read-only connection -> timeout -> at most 20 rows back

dispatch() never raises for a bad call: an unknown tool name, arguments that are not JSON, or
arguments that fail Pydantic validation come back as an error ToolResult, which the agent sends to
the model as the tool message so it can correct itself. Rows from the database are data, not
instructions; they reach the model as JSON values only.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from thai_shop_agent.config import QUERY_TIMEOUT_S
from thai_shop_agent.db import run_query, table_ddl
from thai_shop_agent.retrieval import Retriever, build_schema_context
from thai_shop_agent.schemas import MAX_SEARCH_K, RunSqlArgs, SearchSchemaArgs
from thai_shop_agent.sql_guard import GuardError, prepare

MAX_ROWS_TO_MODEL = 20
MAX_CELL_CHARS = 200

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "search_schema",
            "description": "Look up more of the database documentation: table definitions (DDL), columns, "
                           "join paths and business rules. Use it when the context you have is not enough.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "what to look up, e.g. 'province names' or 'refunds'"},
                    "k": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_K,
                          "description": "how many sections to return (default 3)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Run one read-only SQLite SELECT query on the shop database. Returns the column names, "
                           f"up to {MAX_ROWS_TO_MODEL} rows and the total row count, or an error message to fix.",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string", "description": "a single SQLite SELECT statement"}},
                "required": ["sql"],
            },
        },
    },
]


@dataclass
class ToolResult:
    name: str
    ok: bool
    content: str  # what the model sees as the tool message
    error: str | None = None
    sql: str | None = None  # run_sql: the executed query (after guard + LIMIT)
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)  # run_sql: every returned row (<= LIMIT)
    chunk_ids: list[str] = field(default_factory=list)  # search_schema: chunks returned

    def summary(self) -> dict:
        """Event payload: enough for the UI, without the full row set."""
        out: dict[str, Any] = {"name": self.name, "ok": self.ok, "error": self.error}
        if self.name == "run_sql" and self.ok:
            out.update(sql=self.sql, columns=self.columns, rows=self.rows[:MAX_ROWS_TO_MODEL], row_count=len(self.rows))
        if self.name == "search_schema" and self.ok:
            out["chunks"] = self.chunk_ids
        return out


def _cell(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS] + "…"
    if isinstance(value, float):
        return round(value, 4)
    return value


def _error(name: str, message: str) -> ToolResult:
    return ToolResult(name=name, ok=False, error=message, content=json.dumps({"error": message}, ensure_ascii=False))


def _validation_message(e: ValidationError) -> str:
    parts = [f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}" for err in e.errors()]
    return "invalid arguments: " + "; ".join(parts)


class Tools:
    def __init__(self, retriever: Retriever, conn: sqlite3.Connection, min_score: float, rule_tables: bool = True,
                 timeout_s: float = QUERY_TIMEOUT_S):
        self.retriever = retriever
        self.conn = conn  # opened with db.connect_ro (mode=ro)
        self.ddl = table_ddl(conn)
        self.min_score = min_score
        self.rule_tables = rule_tables
        self.timeout_s = timeout_s
        self._models: dict[str, type[BaseModel]] = {"search_schema": SearchSchemaArgs, "run_sql": RunSqlArgs}

    def dispatch(self, name: str, arguments: str) -> ToolResult:
        model = self._models.get(name)
        if model is None:
            return _error(name, f"unknown tool {name!r}; available tools: {', '.join(self._models)}")
        try:
            raw = json.loads(arguments or "{}")
        except json.JSONDecodeError as e:
            return _error(name, f"arguments are not valid JSON: {e}")
        try:
            args = model.model_validate(raw)
        except ValidationError as e:
            return _error(name, _validation_message(e))
        return self.search_schema(args) if isinstance(args, SearchSchemaArgs) else self.run_sql(args)

    def search_schema(self, args: SearchSchemaArgs) -> ToolResult:
        vector = self.retriever.embed_query(args.query)
        ctx = build_schema_context(self.retriever, vector, args.k, self.min_score, self.ddl, self.rule_tables)
        ids = [h.chunk.id for h in ctx.hits]
        if not ids:
            return ToolResult(name="search_schema", ok=True, content="no matching schema documentation")
        return ToolResult(name="search_schema", ok=True, content=ctx.text, chunk_ids=ids)

    def run_sql(self, args: RunSqlArgs) -> ToolResult:
        try:
            sql = prepare(args.sql)
        except GuardError as e:
            return _error("run_sql", f"query rejected: {e}")
        try:
            columns, rows = run_query(self.conn, sql, self.timeout_s)
        except Exception as e:  # noqa: BLE001 - sqlite errors and timeouts go back to the model
            return _error("run_sql", f"{type(e).__name__}: {e}")
        rows = [list(r) for r in rows]
        payload = {
            "columns": columns,
            "rows": [[_cell(v) for v in r] for r in rows[:MAX_ROWS_TO_MODEL]],
            "row_count": len(rows),
            "truncated": len(rows) > MAX_ROWS_TO_MODEL,
        }
        return ToolResult(name="run_sql", ok=True, content=json.dumps(payload, ensure_ascii=False), sql=sql,
                          columns=columns, rows=rows)
