"""Checks every model-written SQL query must pass before it is executed.

    prepare(text) = add_limit(guard(clean(text)))

clean     strip a ``` code fence and trailing semicolons
guard     one statement, starts with SELECT / WITH, no write/admin keyword, only the shop's tables
add_limit append LIMIT 100 when the outermost query has none

The checks work on sqlglot tokens and the parsed tree (dialect sqlite), not on regexes over the raw
text, so string literals and comments cannot hide or fake a keyword. A failed check raises
GuardError with a message meant to be sent back to the model so it can fix the query.
"""

import logging
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import Token, TokenType

from thai_shop_agent.config import TABLES

ALLOWED_TABLES = frozenset(TABLES)
DENYLIST = frozenset({
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REPLACE",
})
DEFAULT_LIMIT = 100
DIALECT = "sqlite"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")  # sqlglot underlines the error position with terminal codes
_FENCE = re.compile(r"```[ \t]*(?:sqlite|sql)?[ \t]*\n?(.*?)```", re.IGNORECASE | re.DOTALL)

# sqlglot logs a warning when it falls back to parsing a statement as a generic Command;
# for the guard that statement is rejected anyway.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


class GuardError(ValueError):
    """The query was rejected; str(error) explains why in terms the model can act on."""


def strip_code_fence(text: str) -> str:
    """Return the SQL inside the first ``` fence if there is one, otherwise the text itself, trimmed."""
    m = _FENCE.search(text)
    if m:
        return m.group(1).strip()
    # an unterminated fence (output cut off) still starts with ```
    return re.sub(r"^```[ \t]*(?:sqlite|sql)?", "", text.strip(), flags=re.IGNORECASE).strip()


def clean(text: str) -> str:
    """Strip a code fence, then any trailing semicolons and whitespace."""
    return strip_code_fence(text).rstrip().rstrip(";").rstrip()


def _tokenize(sql: str) -> list[Token]:
    try:
        return sqlglot.tokenize(sql, read=DIALECT)
    except SqlglotError as e:
        raise GuardError(f"could not read the SQL: {e}") from e


def _check_tokens(tokens: list[Token]) -> None:
    if not tokens:
        raise GuardError("the query is empty; write one SQLite SELECT statement")
    if any(t.token_type == TokenType.SEMICOLON for t in tokens):
        raise GuardError("only one SQL statement is allowed; remove the ';' and everything after it")
    if tokens[0].token_type not in (TokenType.SELECT, TokenType.WITH):
        raise GuardError(f"the query must start with SELECT or WITH, not {tokens[0].text!r}")
    for i, t in enumerate(tokens):
        if t.token_type in (TokenType.STRING, TokenType.IDENTIFIER):  # literals and "quoted" names
            continue
        word = t.text.upper()
        if word not in DENYLIST:
            continue
        # REPLACE(str, from, to) is a read-only string function; REPLACE INTO is a write
        if word == "REPLACE" and i + 1 < len(tokens) and tokens[i + 1].token_type == TokenType.L_PAREN:
            continue
        raise GuardError(f"{word} is not allowed; only read-only SELECT queries can be run")


def _check_tree(sql: str) -> exp.Expression:
    try:
        trees = [t for t in sqlglot.parse(sql, read=DIALECT) if t is not None]
    except SqlglotError as e:
        raise GuardError(f"SQL syntax error: {_ANSI.sub('', str(e))}") from e
    if len(trees) != 1:
        raise GuardError("only one SQL statement is allowed")
    tree = trees[0]
    if not isinstance(tree, exp.Query):
        raise GuardError(f"only SELECT queries are allowed, got {tree.key.upper()}")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise GuardError(f"table-valued functions are not allowed: {table.this.sql(dialect=DIALECT)}")
        name = table.name.lower()
        if table.db and table.db.lower() != "main":
            raise GuardError(f"table {table.db}.{table.name} is not allowed; use only: {', '.join(sorted(ALLOWED_TABLES))}")
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise GuardError(f"table {table.name!r} does not exist or is not allowed; "
                             f"use only: {', '.join(sorted(ALLOWED_TABLES))}")
    return tree


def guard(sql: str) -> str:
    """Raise GuardError unless ``sql`` is a single read-only query over the allowed tables; return it unchanged."""
    _check_tokens(_tokenize(sql))
    _check_tree(sql)
    return sql


def add_limit(sql: str, limit: int = DEFAULT_LIMIT) -> str:
    """Append LIMIT when the outermost query has none (a LIMIT inside a subquery or CTE does not count).

    Appended as text on a new line, so the query is not re-rendered and a trailing -- comment
    cannot swallow it.
    """
    tree = sqlglot.parse_one(sql, read=DIALECT)
    if tree.args.get("limit") is not None:
        return sql
    return f"{sql}\nLIMIT {limit}"


def prepare(text: str, limit: int = DEFAULT_LIMIT) -> str:
    """clean → guard → add_limit: the only path from model output to an executable query."""
    return add_limit(guard(clean(text)), limit)


def referenced_tables(sql: str) -> list[str]:
    """Sorted base tables a guarded query reads (CTE names excluded)."""
    tree = sqlglot.parse_one(sql, read=DIALECT)
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    return sorted({t.name.lower() for t in tree.find_all(exp.Table)} - cte_names)
