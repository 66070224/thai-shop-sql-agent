import pytest

from thai_shop_agent.config import DEFAULT_RETRIEVAL, PINNED_RULES, SCHEMA_DOCS, RetrievalParams
from thai_shop_agent.retrieval import (Chunk, Retriever, build_index, build_prompt_context, build_schema_context,
                                       example_chunks, schema_chunks)

from test_retrieval import SOURCES, FakeEmbedder

CHUNKS = [
    Chunk(id="table:orders", kind="table", text="orders orders sales", table="orders"),
    Chunk(id="table:customers", kind="table", text="customers province", table="customers"),
    Chunk(id="table:payments", kind="table", text="payment payment", table="payments"),
    Chunk(id="relationships", kind="relationships", text="## Relationships\norders customers"),
    Chunk(id="rule:sales", kind="rule", text="## Rule: sales\nsales sales sales", tables=("orders",)),
    Chunk(id="rule:province", kind="rule", text="## Rule: province\nprovince", tables=("customers",)),
    *example_chunks([{"id": "ex01", "question": "province customers", "sql": "SELECT 1", "tables": ["customers"]}]),
]
DDL = {t: f"CREATE TABLE {t} (...);" for t in ("orders", "customers", "payments")}


@pytest.fixture
def retriever(tmp_path):
    path = tmp_path / "index.json"
    build_index(FakeEmbedder(), CHUNKS, SOURCES, path=path, model="fake")
    return Retriever(FakeEmbedder(), path=path, expected_sources=SOURCES)


def test_pinned_rule_ids_exist_in_schema_docs():
    ids = {c.id for c in schema_chunks(SCHEMA_DOCS.read_text(encoding="utf-8")) if c.kind == "rule"}
    assert set(PINNED_RULES) <= ids


def test_default_has_no_pinned_rules():
    assert RetrievalParams(k_schema=1, k_examples=0, min_score=0.0).pinned_rules == ()
    assert isinstance(DEFAULT_RETRIEVAL.pinned_rules, tuple)


@pytest.mark.parametrize("question", ["payment", "customers province", "orders sales", "nothing matches"])
def test_pinned_rule_always_in_context_even_below_min_score(retriever, question):
    vec = retriever.embed_query(question)
    ctx = build_schema_context(retriever, vec, k=2, min_score=1.01, ddl=DDL, pinned=("rule:sales",))
    assert "## Rule: sales" in ctx.text
    assert ctx.chunk_ids[:2] == ["relationships", "rule:sales"]
    assert "orders" in ctx.tables  # rule_tables applies to pinned rules too
    assert build_schema_context(retriever, vec, k=2, min_score=1.01, ddl=DDL, pinned=("rule:sales",),
                                rule_tables=False).tables == []


def test_pinned_rule_is_not_duplicated_and_k_counts_ranked_chunks(retriever):
    vec = retriever.embed_query("sales sales orders")
    plain = build_schema_context(retriever, vec, k=2, min_score=0.0, ddl=DDL)
    assert "rule:sales" in plain.chunk_ids
    ctx = build_schema_context(retriever, vec, k=2, min_score=0.0, ddl=DDL, pinned=("rule:sales",))
    assert len(ctx.chunk_ids) == len(set(ctx.chunk_ids))
    assert ctx.text.count("## Rule: sales") == 1
    assert len(ctx.hits) == 2 and all(h.chunk.id != "rule:sales" for h in ctx.hits)


def test_pinned_after_relationships_before_ranked(retriever):
    ctx = build_prompt_context(retriever, "customers province", k_schema=1, k_examples=1, min_score=0.0, ddl=DDL,
                               pinned=("rule:sales", "rule:province"))
    assert ctx.chunk_ids == ["relationships", "rule:sales", "rule:province", "table:customers", "ex01"]
    docs = ctx.text.split("## Schema documentation and business rules")[1]
    assert docs.index("## Relationships") < docs.index("## Rule: sales") < docs.index("## Rule: province")


def test_empty_pinned_is_byte_for_byte_unchanged(retriever):
    for q in ["payment", "customers province", "orders sales"]:
        a = build_prompt_context(retriever, q, k_schema=2, k_examples=1, min_score=0.0, ddl=DDL)
        b = build_prompt_context(retriever, q, k_schema=2, k_examples=1, min_score=0.0, ddl=DDL, pinned=())
        assert a.text == b.text and a.chunk_ids == b.chunk_ids and a.schema.tables == b.schema.tables


def test_unknown_or_non_rule_pinned_id_is_an_error(retriever):
    vec = retriever.embed_query("sales")
    with pytest.raises(ValueError, match="rule:nope"):
        build_schema_context(retriever, vec, k=1, min_score=0.0, ddl=DDL, pinned=("rule:nope",))
    with pytest.raises(ValueError, match="table:orders"):
        build_schema_context(retriever, vec, k=1, min_score=0.0, ddl=DDL, pinned=("table:orders",))


def _load(name):
    import importlib.util
    import sys
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "eval" / f"{name}.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(f"eval_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("config", ["b1", "b2", "full"])
def test_run_pin_rules_flag_sets_pinned_rules(config):
    import argparse
    run = _load("run")
    ap = argparse.ArgumentParser()
    ns = lambda pin: argparse.Namespace(config=config, k_schema=None, k_examples=None, min_score=None,  # noqa: E731
                                        no_rule_tables=False, pin_rules=pin)
    assert run.retrieval_params(ap, ns(True)).pinned_rules == PINNED_RULES
    assert run.retrieval_params(ap, ns(False)).pinned_rules == ()
    assert run.retrieval_params(ap, ns(None)).pinned_rules == DEFAULT_RETRIEVAL.pinned_rules


def test_compare_treats_missing_pinned_rules_as_none_and_separates_pinned_runs():
    from dataclasses import asdict
    compare = _load("compare")
    old = {k: v for k, v in asdict(DEFAULT_RETRIEVAL).items() if k != "pinned_rules"}
    pinned = {**asdict(DEFAULT_RETRIEVAL), "pinned_rules": list(PINNED_RULES)}
    unpinned = {**asdict(DEFAULT_RETRIEVAL), "pinned_rules": []}
    expected = compare.expected_params("b2")
    default_pinned = bool(DEFAULT_RETRIEVAL.pinned_rules)
    assert (compare.run_params(old) == expected) is not default_pinned
    assert (compare.run_params(unpinned) == expected) is not default_pinned
    assert (compare.run_params(pinned) == expected) is default_pinned
