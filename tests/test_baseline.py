import sqlite3

from thai_shop_agent.baseline import SYSTEM_PROMPT, RetrievalBaseline, build_b0_messages
from thai_shop_agent.config import RetrievalParams
from thai_shop_agent.llm import Completion
from thai_shop_agent.retrieval import Chunk, Retriever, build_index, example_chunks

VOCAB = ["orders", "customers", "province", "sales"]
SOURCES = {"schema_docs.md": "x", "examples.jsonl": "y"}


class FakeEmbedder:
    def embed(self, texts):
        return [[float(t.lower().count(w)) for w in VOCAB] + [0.1] for t in texts]


class FakeLLM:
    def __init__(self, text="```sql\nSELECT 1;\n```"):
        self.text = text
        self.messages = []

    def chat(self, messages):
        self.messages.append(messages)
        return Completion(text=self.text, prompt_tokens=123, completion_tokens=7)


def make_baseline(tmp_path, k_examples):
    chunks = [
        Chunk(id="table:orders", kind="table", text="## Table: orders\norders sales", table="orders"),
        Chunk(id="table:customers", kind="table", text="## Table: customers\ncustomers province", table="customers"),
        Chunk(id="relationships", kind="relationships", text="## Relationships\njoin paths"),
        *example_chunks([{"id": "ex07", "question": "customers per province", "sql": "SELECT province FROM customers",
                          "tables": ["customers"]}]),
    ]
    path = tmp_path / "index.json"
    build_index(FakeEmbedder(), chunks, SOURCES, path=path)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (order_id INTEGER)")
    conn.execute("CREATE TABLE customers (customer_id INTEGER, province TEXT)")
    llm = FakeLLM()
    params = RetrievalParams(k_schema=1, k_examples=k_examples, min_score=0.0)
    return RetrievalBaseline(llm, conn, Retriever(FakeEmbedder(), path=path, expected_sources=SOURCES), params), llm


def test_b1_prompt_has_retrieved_schema_only(tmp_path):
    b1, llm = make_baseline(tmp_path, k_examples=0)
    pred = b1.predict("how many customers per province?")
    system, user = llm.messages[0]
    assert system == {"role": "system", "content": SYSTEM_PROMPT}
    text = user["content"]
    assert "CREATE TABLE customers" in text and "CREATE TABLE orders" not in text
    assert "## Relationships" in text and "Similar questions" not in text
    assert text.endswith("## Question\n\nhow many customers per province?\n\nWrite the SQL query.")
    assert (pred.sql, pred.prompt_tokens, pred.llm_calls) == ("SELECT 1;", 123, 1)
    assert pred.context_ids == ["relationships", "table:customers"]


def test_b2_adds_examples_before_question(tmp_path):
    b2, llm = make_baseline(tmp_path, k_examples=1)
    pred = b2.predict("how many customers per province?")
    text = llm.messages[0][1]["content"]
    assert "Question: customers per province\nSQL: SELECT province FROM customers" in text
    assert text.index("Similar questions") < text.index("## Question")
    assert pred.context_ids == ["relationships", "table:customers", "ex07"]


def test_b0_and_b1_share_system_prompt():
    assert build_b0_messages("ddl", "docs", "q")[0]["content"] == SYSTEM_PROMPT
