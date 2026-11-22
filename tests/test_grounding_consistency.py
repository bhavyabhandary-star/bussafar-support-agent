"""Policy answers must stay grounded and consistent: same question, same help-doc facts."""
from __future__ import annotations

import config
from agent.sahil import Sahil, ungrounded_facts
from tests.conftest import FakeLLM, text

GOOD = ("If you cancel more than 24 hours before departure you get a 90% refund, 12 to 24 hours before gets 50%, "
        "and less than 12 hours gets no refund. (Source: Cancellation and refund policy)")


def test_same_policy_question_gets_same_grounded_answer(retriever, tickets):
    q = "More than 24 hours before departure: 90% refund."  # fake embedder matches the policy text
    agent = Sahil(retriever, FakeLLM([text(GOOD)] * 3), tickets, config.BOOKINGS_CSV)
    replies = [agent.respond(q).text for _ in range(3)]
    assert replies == [GOOD] * 3  # not swapped for a clarifying question on the repeat


def test_made_up_refund_figure_is_replaced_with_doc_text(retriever, tickets):
    bad = "You get an 80% refund if you cancel 30 hours before. (Source: Cancellation and refund policy)"
    agent = Sahil(retriever, FakeLLM([text(bad)]), tickets, config.BOOKINGS_CSV)
    r = agent.respond("More than 24 hours before departure: 90% refund.")
    assert "80%" not in r.text and "90%" in r.text and "50%" in r.text
    assert "(Source:" in r.text and "ungrounded_replaced" in r.flags


def test_figures_from_docs_or_customer_are_allowed(retriever):
    chunks = retriever.search("More than 24 hours before departure: 90% refund.")
    assert ungrounded_facts(GOOD, chunks) == []
    assert ungrounded_facts("Cancelling 30 hours before gets 90%.", chunks, "I cancel 30 hours before") == []
    assert ungrounded_facts("You get 75% back.", chunks) == ["75%"]
    assert ungrounded_facts(config.TICKET_REPLY_PROMISE, []) == []


def test_ungrounded_answer_without_matching_docs_falls_back(retriever, tickets):
    agent = Sahil(retriever, FakeLLM([text("Travel insurance costs ₹49.")]), tickets, config.BOOKINGS_CSV)
    r = agent.respond("Explain quantum chromodynamics lattice gauge theory")
    assert "₹49" not in r.text and config.FALLBACK_MESSAGE in r.text


def test_status_meaning_questions_go_to_help_docs(tickets):
    agent = Sahil(None, None, tickets, config.BOOKINGS_CSV)
    for q in ("What does No-Show mean on my booking?", "what does my booking status mean"):
        assert agent.respond(q).flags == ["llm_unavailable"], q  # reaches the help docs, not a PNR prompt
    assert agent.respond("Check my booking status").flags == ["booking_need_pnr"]


def test_refund_of_2000_or_less_is_not_escalated_by_the_model(retriever, tickets):
    from tests.conftest import call
    llm = FakeLLM([call("create_ticket", customer_message="x", reason="refund", category="refund_dispute",
                        priority="normal"),
                   text("Refunds follow the cancellation policy bands. (Source: Cancellation and refund policy)")])
    agent = Sahil(retriever, llm, tickets, config.BOOKINGS_CSV)
    r = agent.respond("I want a refund of ₹2,000 for my trip")
    assert r.ticket_ids == [] and tickets.list_tickets() == []
    tool_result = llm.calls[1]["messages"][-1]["response"]
    assert tool_result["success"] is False and "not escalated" in tool_result["message"]


def test_claim_about_services_not_in_docs_becomes_fallback(retriever, tickets):
    llm = FakeLLM([text("I can only help with BusSafar bus bookings. We do not offer travel insurance.")])
    agent = Sahil(retriever, llm, tickets, config.BOOKINGS_CSV)
    r = agent.respond("Explain quantum chromodynamics lattice gauge theory")  # retrieves nothing
    assert r.text == config.FALLBACK_MESSAGE and "unsupported_claim_replaced" in r.flags
