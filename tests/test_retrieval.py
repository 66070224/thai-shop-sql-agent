from types import SimpleNamespace

import pytest

from thai_shop_agent.config import SCHEMA_DOCS
from thai_shop_agent.llm import Embedder
from thai_shop_agent.retrieval import (Chunk, Retriever, StaleIndexError, build_index, build_prompt_context,
                                       build_schema_context, cosine,
                                       example_chunks, schema_chunks)

VOCAB = ["orders", "customers", "sales", "province", "payment", "review"]


class FakeEmbedder:
    """Deterministic bag-of-words vectors over a tiny vocabulary (+1 bias so no vector is zero)."""

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[float(t.lower().count(w)) for w in VOCAB] + [0.1] for t in texts]


CHUNKS = [
    Chunk(id="table:orders", kind="table", text="orders orders sales", table="orders"),
    Chunk(id="table:customers", kind="table", text="customers province", table="customers"),
    Chunk(id="table:payments", kind="table", text="payment payment", table="payments"),
    Chunk(id="relationships", kind="relationships", text="## Relationships\norders customers"),
    Chunk(id="rule:sales", kind="rule", text="sales sales sales"),
    *example_chunks([{"id": "ex01", "question": "province customers", "sql": "SELECT 1", "tables": ["customers"]}]),
]
SOURCES = {"schema_docs.md": "a", "examples.jsonl": "b"}


@pytest.fixture
def retriever(tmp_path):
    path = tmp_path / "index.json"
    build_index(FakeEmbedder(), CHUNKS, SOURCES, path=path, model="fake")
    return Retriever(FakeEmbedder(), path=path, expected_sources=SOURCES)


def test_schema_docs_split_into_16_chunks():
    chunks = schema_chunks(SCHEMA_DOCS.read_text(encoding="utf-8"))
    kinds = [c.kind for c in chunks]
    assert len(chunks) == 16 and len({c.id for c in chunks}) == 16
    assert kinds.count("table") == 8 and kinds.count("rule") == 7 and kinds.count("relationships") == 1
    assert {c.table for c in chunks if c.kind == "table"} == {
        "orders", "order_items", "products", "categories", "customers", "payments", "reviews", "coupons"}
    assert all(c.text.startswith("## ") for c in chunks)
    assert "rule:sales-revenue-definition" in {c.id for c in chunks}


def test_unknown_heading_is_an_error():
    with pytest.raises(ValueError, match="Glossary"):
        schema_chunks("intro\n## Glossary\ntext")


def test_example_chunk_embeds_question_only():
    (c,) = example_chunks([{"id": "ex09", "question": "q?", "sql": "SELECT 1", "tables": ["orders"]}])
    assert (c.kind, c.text, c.sql, c.tables) == ("example", "q?", "SELECT 1", ("orders",))


def test_cosine():
    assert cosine([1, 0], [2, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 3]) == pytest.approx(0.0)
    assert cosine([0, 0], [1, 1]) == 0.0


def test_embedder_batches_and_restores_input_order():
    class Client:
        def __init__(self):
            self.batches = []
            self.embeddings = self

        def create(self, model, input):
            self.batches.append(list(input))
            data = [SimpleNamespace(index=i, embedding=[float(len(t))]) for i, t in enumerate(input)]
            return SimpleNamespace(data=list(reversed(data)))  # server may return any order

    client = Client()
    vectors = Embedder(batch_size=2, client=client).embed(["a", "bbb", "cc"])
    assert vectors == [[1.0], [3.0], [2.0]]
    assert client.batches == [["a", "bbb"], ["cc"]]


def test_build_index_embeds_all_chunks_in_order(tmp_path, retriever):
    assert [c.id for c in retriever.chunks] == [c.id for c in CHUNKS]
    assert retriever.vectors[0] == FakeEmbedder().embed([CHUNKS[0].text])[0]


def test_search_filters_kind_k_and_min_score(retriever):
    hits = retriever.search("sales of orders", k=2, min_score=0.0, kind=("table", "rule"))
    assert [h.chunk.id for h in hits] == ["table:orders", "rule:sales"]
    assert hits[0].score >= hits[1].score
    assert [h.chunk.id for h in retriever.search("sales", k=5, min_score=0.0, kind="example")] == ["ex01"]
    assert retriever.search("sales", k=5, min_score=0.99, kind="table") == []
    assert all(h.score >= 0.5 for h in retriever.search("payment", k=10, min_score=0.5))


def test_stale_or_missing_index_is_refused(tmp_path):
    path = tmp_path / "index.json"
    build_index(FakeEmbedder(), CHUNKS, SOURCES, path=path)
    with pytest.raises(StaleIndexError):
        Retriever(FakeEmbedder(), path=path, expected_sources={**SOURCES, "examples.jsonl": "changed"})
    with pytest.raises(FileNotFoundError, match="build_index"):
        Retriever(FakeEmbedder(), path=tmp_path / "missing.json", expected_sources=SOURCES)


def test_schema_context_has_relationships_and_only_retrieved_ddl(retriever):
    ddl = {"orders": "CREATE TABLE orders (...);", "customers": "CREATE TABLE customers (...);",
           "payments": "CREATE TABLE payments (...);"}
    ctx = build_schema_context(retriever, retriever.embed_query("customers in a province"), k=1, min_score=0.0,
                               ddl=ddl)
    assert ctx.tables == ["customers"]
    assert "CREATE TABLE customers" in ctx.text and "CREATE TABLE orders" not in ctx.text
    assert "## Relationships" in ctx.text
    assert ctx.chunk_ids == ["relationships", "table:customers"]


def test_rule_chunks_name_their_tables():
    rules = {c.id: c.tables for c in schema_chunks(SCHEMA_DOCS.read_text(encoding="utf-8")) if c.kind == "rule"}
    assert rules["rule:sales-revenue-definition"] == ("orders", "order_items", "products")
    assert rules["rule:province-names"] == ("customers",)


def test_schema_context_adds_ddl_of_tables_named_by_retrieved_rules(tmp_path):
    chunks = CHUNKS[:4] + [Chunk(id="rule:sales", kind="rule", text="sales sales sales", tables=("orders", "payments"))]
    path = tmp_path / "index.json"
    build_index(FakeEmbedder(), chunks, SOURCES, path=path)
    r = Retriever(FakeEmbedder(), path=path, expected_sources=SOURCES)
    ddl = {t: f"CREATE TABLE {t} (...);" for t in ("orders", "customers", "payments")}
    vec = r.embed_query("sales")
    assert build_schema_context(r, vec, k=1, min_score=0.0, ddl=ddl).tables == ["orders", "payments"]
    assert build_schema_context(r, vec, k=1, min_score=0.0, ddl=ddl, rule_tables=False).tables == []


def test_prompt_context_appends_examples(retriever):
    ctx = build_prompt_context(retriever, "customers province", k_schema=1, k_examples=1, min_score=0.0, ddl={})
    assert ctx.text.endswith("Question: province customers\nSQL: SELECT 1")
    assert ctx.chunk_ids == ["relationships", "table:customers", "ex01"]
    no_examples = build_prompt_context(retriever, "customers province", k_schema=1, k_examples=0, min_score=0.0,
                                       ddl={})
    assert no_examples.examples == [] and "Similar questions" not in no_examples.text


def test_old_index_version_is_refused(tmp_path):
    import json
    path = tmp_path / "index.json"
    build_index(FakeEmbedder(), CHUNKS, SOURCES, path=path)
    index = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**index, "version": 1}), encoding="utf-8")
    with pytest.raises(StaleIndexError):
        Retriever(FakeEmbedder(), path=path, expected_sources=SOURCES)
