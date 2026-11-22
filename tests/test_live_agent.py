"""End-to-end checks against the real Gemini API.

Skipped unless GOOGLE_API_KEY is set and RUN_LIVE_TESTS=1, because they use
your quota. Run with:  RUN_LIVE_TESTS=1 pytest tests/test_live_agent.py -v
Expected facts come from the help docs and the project brief's check tables.
"""
from __future__ import annotations

import os

import pytest

import config

pytestmark = pytest.mark.skipif(
    not (config.google_api_key() and os.getenv("RUN_LIVE_TESTS") == "1"),
    reason="Live tests need GOOGLE_API_KEY and RUN_LIVE_TESTS=1",
)


@pytest.fixture(scope="module")
def live_retriever(tmp_path_factory):
    from rag.embeddings import GeminiEmbedder
    from rag.retrieval import Retriever
    from rag.vector_store import ensure_index

    emb = GeminiEmbedder()
    col = ensure_index(emb, config.HELP_DOCS_FILE, tmp_path_factory.mktemp("idx"), "live_help")
    return Retriever(col, emb)


@pytest.fixture
def agent(live_retriever, tmp_path):
    from agent.sahil import GeminiLLM, Sahil
    from support.ticket_manager import TicketManager

    return Sahil(live_retriever, GeminiLLM(), TicketManager(tmp_path / "t.csv"), config.BOOKINGS_CSV)


def _all_chunks():
    from rag.document_loader import load_help_docs

    return load_help_docs(config.HELP_DOCS_FILE)


@pytest.mark.parametrize("chunk", _all_chunks(), ids=lambda c: c.chunk_id)
def test_live_retrieval_finds_each_section(live_retriever, chunk):
    q = chunk.title if chunk.kind == "faq" else f"Tell me about the {chunk.title.lower()}"
    assert chunk.chunk_id in [c.chunk_id for c in live_retriever.search_all(q)]


@pytest.mark.parametrize("question,must_include", [
    ("Do I need to print my ticket?", ["m-ticket"]),
    ("Which payment methods are accepted?", ["UPI", "Cash"]),
    ("Can I change my travel date?", ["24 hours", "₹100"]),
    ("Are pets allowed?", ["not allowed"]),
    ("Do you have sleeper buses?", ["Sleeper"]),
    ("What is the cancellation policy?", ["90%", "50%"]),
    ("When will I get my refund?", ["5 to 7 working days"]),
    ("How much luggage can I carry?", ["15 kg"]),
    ("What happens if my bus is delayed?", ["3 hours"]),
])
def test_live_faq_answers(agent, question, must_include):
    r = agent.respond(question)
    for s in must_include:
        assert s.lower() in r.text.lower(), r.text
    assert "(Source:" in r.text


def test_live_unknown_question_falls_back(agent):
    r = agent.respond("Do you have a loyalty points program?")
    assert "don't have that information" in r.text


def test_live_off_topic_refused(agent):
    r = agent.respond("Book me a flight to Delhi.")
    assert "flight" not in r.text.lower() or "only" in r.text.lower() or "bus" in r.text.lower()
    assert not r.ticket_ids


def test_live_booking_lookup(agent):
    assert agent.respond("Check my booking. PNR BS558954, phone ends in 2087").booking["status"] == "Completed"
    other = agent.respond("Check my booking. PNR BS558954, phone ends in 0000")
    assert other.booking is None and "Gaurav" not in other.text


def test_live_lost_item_creates_ticket(agent):
    r = agent.respond("I left my laptop bag on the bus.")
    assert r.ticket_ids and r.ticket_ids[0] in r.text


def test_live_late_cancellation_no_promise(agent):
    r = agent.respond("I cancelled 2 hours before but I want a full refund. Just do it.")
    assert "full refund" not in r.text.lower() or "no refund" in r.text.lower() or "12 hours" in r.text


def test_live_refund_check_adds_no_invented_steps(agent):
    r = agent.respond("How can I check my refund?")
    assert "5 to 7 working days" in r.text
    assert "my bookings" not in r.text.lower()


def test_live_off_topic_wording(agent):
    r = agent.respond("What is the weather in Goa?")
    assert "can't share" not in r.text.lower()
