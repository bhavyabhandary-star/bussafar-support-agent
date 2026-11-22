"""The live conversation that made Sahil loop (repeated greeting, a new ticket on every angry message).

The fake model below behaves badly on purpose: it greets every time and tries to raise a ticket
whenever it is called. The code must still produce one ticket and no repeated replies.
"""
from __future__ import annotations

import config
from agent import guardrails as g
from agent.sahil import Sahil
from tests.conftest import FakeLLM, call, text

SCREENSHOT = [
    "Hi Sahil, how are you ?",
    "gldhgj hljg fj",
    "WTF",
    "I dont need a ticket",
    "Connect me to an agent",
    "Hello whats wrong with you escalate the issue to your higher authorities",
    "How many tickets will you create.. screw you",
    "why should I wait 24 hrs you sdfdsfs",
]


class GreedyLLM(FakeLLM):
    """Greets on plain turns and tries create_ticket whenever it gets the chance."""

    def __init__(self):
        super().__init__([])

    def generate(self, system, messages, tools):
        self.calls.append({"messages": [dict(m) for m in messages]})
        if messages[-1].get("role") == "tool":
            res = messages[-1]["response"]
            tid = res.get("ticket_id", "")
            return text(f"Your ticket is {tid}. {config.TICKET_REPLY_PROMISE}" if tid else config.GREETING)
        if "ticket" in messages[-1]["text"].lower() or "wait" in messages[-1]["text"].lower():
            return call("create_ticket", customer_message="x", reason="upset", category="complaint", priority="normal")
        return text(config.GREETING)


def test_screenshot_conversation_no_loop(retriever, tickets):
    agent = Sahil(retriever, GreedyLLM(), tickets, config.BOOKINGS_CSV)
    replies = [agent.respond(m) for m in SCREENSHOT]
    texts = [r.text for r in replies]
    created = [t for r in replies for t in r.ticket_ids]
    assert created == ["TKT-1001"], texts           # exactly one ticket, from "Connect me to an agent"
    assert len(tickets.list_tickets()) == 1
    assert config.GREETING not in texts               # never re-greets
    assert all(a != b for a, b in zip(texts, texts[1:])), texts  # never the same reply twice in a row
    assert "didn't quite catch" in texts[1]           # gibberish -> clarify
    assert "TKT" not in texts[2]                      # WTF -> calm reply, no ticket
    assert "TKT-1001" in texts[5] and "TKT-1001" in texts[6] and "TKT-1001" in texts[7]


def test_safety_still_gets_its_own_ticket(retriever, tickets):
    agent = Sahil(retriever, GreedyLLM(), tickets, config.BOOKINGS_CSV)
    assert agent.respond("Connect me to an agent").ticket_ids == ["TKT-1001"]
    r = agent.respond("The driver is driving dangerously right now!")
    assert r.ticket_ids == ["TKT-1002"] and "112" in r.text


def test_detectors():
    assert g.detect_small_talk("Hi Sahil, how are you ?") and g.detect_small_talk("thanks")
    assert not g.detect_small_talk("Hi, can I change my travel date?")
    assert g.detect_gibberish("gldhgj hljg fj") and g.detect_gibberish("sdfdsfs")
    assert not g.detect_gibberish("Do you have sleeper buses?") and not g.detect_gibberish("BS558954 2087")
    assert g.detect_abuse("WTF") and g.detect_abuse("screw you")
    assert not g.detect_abuse("My bus never came")


def test_real_questions_still_reach_the_model(retriever, tickets):
    llm = FakeLLM([text("You can carry one bag up to 15 kg and one small handbag. (Source: FAQ: How much luggage can I carry?)")])
    agent = Sahil(retriever, llm, tickets, config.BOOKINGS_CSV)
    r = agent.respond("Hi! How much luggage can I carry?")
    assert "15 kg" in r.text and llm.calls
