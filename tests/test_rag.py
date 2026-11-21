"""Knowledge base: ingestion, chunking, ChromaDB indexing and retrieval."""
from __future__ import annotations

import pytest

import config
from rag.document_loader import (
    HelpDocsFormatError, load_help_docs, parse_help_docs,
)
from rag.retrieval import Retriever, format_context
from rag.vector_store import ensure_index, get_collection

CHUNKS = load_help_docs(config.HELP_DOCS_FILE)
SOURCE_TEXT = config.HELP_DOCS_FILE.read_text(encoding="utf-8")

EXPECTED_POLICIES = [
    "Cancellation and refund policy",
    "Travel rules",
    "Delays and operator cancellations",
]


def test_has_all_20_faqs_and_3_policies():
    assert sum(c.kind == "faq" for c in CHUNKS) == 20
    assert [c.title for c in CHUNKS if c.kind == "policy"] == EXPECTED_POLICIES
    assert len({c.chunk_id for c in CHUNKS}) == 23


@pytest.mark.parametrize("chunk", CHUNKS, ids=[c.chunk_id for c in CHUNKS])
def test_every_chunk_is_verbatim_from_source(chunk):
    # Each line of a chunk must exist in the help docs exactly as written.
    for line in chunk.text.splitlines():
        assert line in SOURCE_TEXT, f"{chunk.chunk_id}: line not in source: {line!r}"
    assert chunk.source and chunk.metadata["source_file"] == "bussafar_help.txt"


def test_key_facts_preserved_exactly():
    by_title = {c.title: c.text for c in CHUNKS}
    assert "₹100 fee plus any fare difference" in by_title["Can I change my travel date?"]
    assert "90%" in by_title["How much refund will I get if I cancel?"]
    assert "5 to 7 working days" in by_title["When will I get my refund?"]
    assert "15 kg" in by_title["How much luggage can I carry?"]
    assert "112" in by_title["Delays and operator cancellations"]


def test_strict_loader_rejects_wrong_counts(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("Q: One?\nA: Yes.\nPolicy: Only one\nA rule.\n", encoding="utf-8")
    with pytest.raises(HelpDocsFormatError):
        load_help_docs(bad)
    assert len(load_help_docs(bad, strict=False)) == 2


def test_faq_without_answer_is_rejected():
    with pytest.raises(HelpDocsFormatError):
        parse_help_docs("Q: Missing answer?\n\nQ: Next?\nA: Yes.")


def test_index_stores_all_chunks_with_metadata(retriever):
    col = retriever.collection
    assert col.count() == 23
    got = col.get(ids=["policy-01"], include=["metadatas", "documents"])
    assert got["metadatas"][0]["source"] == "Cancellation and refund policy"
    assert got["metadatas"][0]["kind"] == "policy"


@pytest.mark.parametrize("chunk", CHUNKS, ids=[c.chunk_id for c in CHUNKS])
def test_each_faq_and_policy_is_retrieved_top1(retriever, chunk):
    query = chunk.title if chunk.kind == "faq" else chunk.text
    results = retriever.search(query)
    assert results, f"nothing retrieved for {query!r}"
    assert results[0].chunk_id == chunk.chunk_id
    assert len(results) <= 3


def test_unrelated_question_returns_no_context(retriever):
    assert retriever.search("Explain quantum chromodynamics lattice gauge theory") == []
    assert format_context([]) == "NO RELEVANT HELP-DOC SECTIONS FOUND."


def test_context_carries_source_labels(retriever):
    ctx = format_context(retriever.search("Can I change my travel date?"))
    assert "[Source: FAQ: Can I change my travel date?]" in ctx


def test_index_rebuilds_when_help_docs_change(fake_embedder, tmp_path):
    src = tmp_path / "help.txt"
    src.write_text(SOURCE_TEXT, encoding="utf-8")
    d = tmp_path / "idx"
    col = ensure_index(fake_embedder, src, d, "col1")
    fp1 = col.metadata["fingerprint"]
    assert ensure_index(fake_embedder, src, d, "col1").metadata["fingerprint"] == fp1
    src.write_text(SOURCE_TEXT.replace("15 minutes", "15 minutes "), encoding="utf-8")
    assert ensure_index(fake_embedder, src, d, "col1").metadata["fingerprint"] != fp1
    assert get_collection(d, "col1").count() == 23


def test_empty_query_returns_nothing(retriever):
    assert retriever.search("   ") == []
