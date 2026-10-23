"""Agent loop with a scripted fake LLM (no proxy, in-memory SQLite)."""

import json
import sqlite3

import pytest

from thai_shop_agent.agent import Agent
from thai_shop_agent.config import RetrievalParams
from thai_shop_agent.llm import ToolCall, ToolCompletion
from thai_shop_agent.retrieval import Chunk, Retriever, build_index, example_chunks
from thai_shop_agent.schemas import FinalAnswer
from thai_shop_agent.tools import TOOL_SPECS, Tools

VOCAB = ["orders", "customers", "province", "sales"]
SOURCES = {"schema_docs.md": "x", "examples.jsonl": "y"}


class FakeEmbedder:
    def embed(self, texts):
        return [[float(t.lower().count(w)) for w in VOCAB] + [0.1] for t in texts]


def call(name, args, id_="c1"):
    return ToolCall(id=id_, name=name, arguments=args if isinstance(args, str) else json.dumps(args))


def reply(text="", *calls, tokens=(100, 10)):
    return ToolCompletion(text=text, prompt_tokens=tokens[0], completion_tokens=tokens[1], tool_calls=list(calls))


class FakeLLM:
    """Returns the scripted completions in order; repeats the last one when the script runs out."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def chat_tools(self, messages, tools):
        assert tools == TOOL_SPECS
        self.calls.append([dict(m) for m in messages])
        return self.script[min(len(self.calls), len(self.script)) - 1]


@pytest.fixture
def setup(tmp_path):
    chunks = [
        Chunk(id="table:orders", kind="table", text="## Table: orders\norders sales", table="orders"),
        Chunk(id="table:customers", kind="table", text="## Table: customers\ncustomers province", table="customers"),
        Chunk(id="relationships", kind="relationships", text="## Relationships\njoin paths"),
        Chunk(id="rule:province", kind="rule", text="## Rule: province\nprovince province", tables=("customers",)),
        *example_chunks([{"id": "ex01", "question": "count orders", "sql": "SELECT COUNT(*) FROM orders",
                          "tables": ["orders"]}]),
    ]
    path = tmp_path / "index.json"
    build_index(FakeEmbedder(), chunks, SOURCES, path=path)
    retriever = Retriever(FakeEmbedder(), path=path, expected_sources=SOURCES)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (order_id INTEGER PRIMARY KEY, status TEXT)")
    conn.executemany("INSERT INTO orders VALUES (?, ?)", [(i, "delivered") for i in range(1, 31)])
    return retriever, conn


def make_agent(setup, llm, max_steps=5):
    retriever, conn = setup
    return Agent(llm, retriever, conn, RetrievalParams(k_schema=2, k_examples=1, min_score=0.0), max_steps=max_steps)


def run(agent, question="how many orders?"):
    events = list(agent.run(question))
    return events, (FinalAnswer.model_validate(events[-1]) if events[-1]["type"] == "answer" else None)


def test_stops_when_model_answers_without_tool_call(setup):
    llm = FakeLLM(reply("", call("run_sql", {"sql": "SELECT COUNT(*) AS n FROM orders"})),
                  reply("There are 30 orders."))
    events, final = run(make_agent(setup, llm))
    assert [e["type"] for e in events] == ["retrieval", "tool_call", "tool_result", "answer"]
    assert final.answer == "There are 30 orders." and final.stopped_reason == "answered"
    assert final.sql == "SELECT COUNT(*) AS n FROM orders\nLIMIT 100"
    assert (final.columns, final.rows) == (["n"], [[30]])
    assert final.steps == 1 and final.usage.model_dump() == {"llm_calls": 2, "prompt_tokens": 200,
                                                              "completion_tokens": 20}
    assert final.citations.tables == ["orders"]
    assert final.citations.chunks[0] == "relationships" and "ex01" in final.citations.chunks
    # the starting context is the B2 context, and the tool result went back as a tool message
    first_user = llm.calls[0][1]["content"]
    assert "Similar questions" in first_user and first_user.endswith("## Question\n\nhow many orders?")
    tool_msg = llm.calls[1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    assert json.loads(tool_msg["content"])["rows"] == [[30]]


def test_stops_at_max_steps(setup):
    script = [reply("", call("run_sql", {"sql": f"SELECT {i} AS x FROM orders LIMIT 1"}, f"c{i}")) for i in range(9)]
    llm = FakeLLM(*script)
    events, final = run(make_agent(setup, llm, max_steps=3))
    assert len(llm.calls) == 3 and final.usage.llm_calls == 3
    assert final.stopped_reason == "max_steps" and final.steps == 3
    assert final.sql == "SELECT 2 AS x FROM orders LIMIT 1"
    assert final.answer.startswith("Stopped before a final answer (max_steps)")


def test_unknown_tool_is_reported_back_not_raised(setup):
    llm = FakeLLM(reply("", call("drop_database", {"name": "shop"})), reply("I cannot do that."))
    events, final = run(make_agent(setup, llm))
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] is False and "unknown tool 'drop_database'" in result["error"]
    assert "unknown tool" in json.loads(llm.calls[1][-1]["content"])["error"]
    assert final.sql is None and final.stopped_reason == "answered"


@pytest.mark.parametrize("args, message", [
    ({"query": "SELECT 1"}, "invalid arguments"),  # wrong field name
    ({"sql": ""}, "invalid arguments: sql"),
    ("{not json", "not valid JSON"),
])
def test_bad_arguments_are_reported_back(setup, args, message):
    llm = FakeLLM(reply("", call("run_sql", args)), reply("ok"))
    events, final = run(make_agent(setup, llm))
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] is False and message in result["error"]
    assert final.sql is None


def test_sql_error_then_fixed_in_next_round(setup):
    llm = FakeLLM(reply("", call("run_sql", {"sql": "SELECT nope FROM orders"}, "a")),
                  reply("", call("run_sql", {"sql": "SELECT COUNT(*) FROM orders"}, "b")),
                  reply("30"))
    events, final = run(make_agent(setup, llm))
    results = [e for e in events if e["type"] == "tool_result"]
    assert results[0]["ok"] is False and "no such column: nope" in results[0]["error"]
    assert "no such column" in llm.calls[1][-1]["content"]
    assert results[1]["ok"] is True
    assert final.sql == "SELECT COUNT(*) FROM orders\nLIMIT 100" and final.rows == [[30]]
    assert final.stopped_reason == "answered" and final.steps == 2


def test_repeating_the_same_sql_stops_with_no_progress(setup):
    same = {"sql": "SELECT nope FROM orders"}
    llm = FakeLLM(reply("", call("run_sql", same, "a")), reply("", call("run_sql", {"sql": " select NOPE from orders; "}, "b")))
    events, final = run(make_agent(setup, llm))
    assert final.stopped_reason == "no_progress" and final.steps == 1 and len(llm.calls) == 2
    assert [e["type"] for e in events].count("tool_call") == 1


def test_guard_rejection_goes_back_to_the_model(setup):
    llm = FakeLLM(reply("", call("run_sql", {"sql": "DROP TABLE orders"})), reply("cannot"))
    events, _ = run(make_agent(setup, llm))
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] is False and result["error"].startswith("query rejected: the query must start with SELECT")
    assert setup[1].execute("SELECT COUNT(*) FROM orders").fetchone() == (30,)


def test_run_sql_sends_at_most_20_rows_to_the_model(setup):
    retriever, conn = setup
    result = Tools(retriever, conn, min_score=0.0).dispatch("run_sql", json.dumps({"sql": "SELECT order_id FROM orders"}))
    payload = json.loads(result.content)
    assert (len(payload["rows"]), payload["row_count"], payload["truncated"]) == (20, 30, True)
    assert len(result.rows) == 30 and result.summary()["row_count"] == 30


def test_search_schema_results_are_cited(setup):
    llm = FakeLLM(reply("", call("search_schema", {"query": "province names", "k": 1})),
                  reply("", call("run_sql", {"sql": "SELECT COUNT(*) FROM orders"}, "c2")), reply("done"))
    events, final = run(make_agent(setup, llm))
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] is True and result["chunks"] == ["rule:province"]
    assert "rule:province" in final.citations.chunks
    assert "CREATE TABLE" in llm.calls[1][-1]["content"] or "Rule: province" in llm.calls[1][-1]["content"]


def test_search_schema_k_is_validated(setup):
    retriever, conn = setup
    result = Tools(retriever, conn, min_score=0.0).dispatch("search_schema", json.dumps({"query": "x", "k": 99}))
    assert result.ok is False and "k" in result.error


def test_llm_failure_ends_with_error_event(setup):
    class Broken:
        def chat_tools(self, messages, tools):
            raise ConnectionError("proxy unreachable")

    events, final = run(make_agent(setup, Broken()))
    assert [e["type"] for e in events] == ["retrieval", "error"]
    assert final is None and events[-1]["message"] == "ConnectionError: proxy unreachable"
