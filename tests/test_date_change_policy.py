"""Date change is allowed once, only up to 24 hours before departure.

Bug: "My bus leaves in 12 hours. Can I change my travel date?" got "Yes, you can change your travel
date once, as it is 12 hours before departure, for a ₹100 fee plus any fare difference (Source:
Cancellation and refund policy)". The model mixed up the date-change line with the refund band
"12 to 24 hours before departure" in the same section. Code now compares the hours with 24.
"""
from __future__ import annotations

import pytest

import config
from agent.policy_check import check_date_change, contradicts, hours_to_departure
from agent.sahil import Sahil
from tests.conftest import FakeLLM, text

BUGGY = ("Yes, you can change your travel date once, as it is 12 hours before departure, for a ₹100 fee "
         "plus any fare difference (Source: Cancellation and refund policy)")


@pytest.mark.parametrize("msg,hours", [
    ("My bus leaves in 12 hours. Can I change my travel date?", 12),
    ("My bus leaves in exactly 24 hours. Can I change my travel date?", 24),
    ("My bus leaves in 30 hours. Can I change my travel date?", 30),
    ("bus departs in 23 hrs, can i reschedule?", 23),
    ("My trip starts in two days, can I move the date?", 48),
    ("My bus leaves in 45 minutes, can I change the date?", 0.75),
    ("Departure is 12h away, can I change my journey date?", 12),
])
def test_hours_parsed(msg, hours):
    assert hours_to_departure(msg) == pytest.approx(hours)


def test_less_than_and_more_than_24():
    assert check_date_change("My bus leaves in less than 24 hours. Can I change the date?").allowed is False
    assert check_date_change("My bus leaves in more than 24 hours. Can I change the date?").allowed is True


@pytest.mark.parametrize("msg", [
    "Can I change my travel date?",                                   # no hours: answer from the docs
    "My bus is delayed by 3 hours. Can I change my travel date?",     # a delay, not time to departure
    "My bus leaves in 30 hours. I already changed the date once. Can I change it again?",
    "My bus leaves in 12 hours. Can I cancel?",                       # cancellation, not a date change
])
def test_no_check_when_not_a_stated_departure_date_change(msg):
    assert check_date_change(msg) is None


@pytest.mark.parametrize("hours,allowed", [(12, False), (23, False), (23.5, False), (24, True), (30, True), (48, True)])
def test_cutoff_boundaries(hours, allowed):
    dc = check_date_change(f"My bus leaves in {hours} hours. Can I change my travel date?")
    assert dc.allowed is allowed


def test_follow_up_turns():
    assert check_date_change("Can I change the date?", ["My bus leaves in 12 hours."]).allowed is False
    assert check_date_change("It leaves in 30 hours", ["Can I change my travel date?"]).allowed is True


def test_contradiction_detection():
    no = check_date_change("My bus leaves in 12 hours. Can I change my travel date?")
    yes = check_date_change("My bus leaves in 30 hours. Can I change my travel date?")
    assert contradicts(BUGGY, no)
    assert contradicts("You can change your date once for ₹100. (Source: FAQ: Can I change my travel date?)", no)
    assert not contradicts("No, date changes are only allowed up to 24 hours before departure, so you "
                           "cannot change your date now. (Source: FAQ: Can I change my travel date?)", no)
    assert not contradicts("Yes, you can change your travel date once, for ₹100 plus any fare difference. "
                           "You cannot change it a second time. (Source: Cancellation and refund policy)", yes)
    assert contradicts("No, you can no longer change the date. (Source: Cancellation and refund policy)", yes)
    # A "No" without the 24-hour reason is replaced by the reply that states it.
    assert contradicts("No, because your bus leaves in 12 hours, you can no longer change your travel date. "
                       "(Source: Cancellation and refund policy)", no)


def _agent(retriever, tickets, reply):
    llm = FakeLLM([text(reply)])
    return Sahil(retriever, llm, tickets, config.BOOKINGS_CSV), llm


def test_buggy_yes_at_12_hours_is_replaced(retriever, tickets):
    agent, llm = _agent(retriever, tickets, BUGGY)
    r = agent.respond("My bus leaves in 12 hours. Can I change my travel date?")
    assert r.text.startswith("No, a date change is no longer available")
    assert "24 hours" in r.text and "50% refund" in r.text and "person" in r.text
    assert "(Source: Cancellation and refund policy)" in r.text
    assert "Cancellation and refund policy" in r.sources
    assert "policy_check_replaced" in r.flags
    sent = llm.calls[0]["messages"][-1]["text"]
    assert "POLICY CHECK" in sent and "NOT available" in sent and "Policy: Cancellation and refund policy" in sent


def test_under_12_hours_offers_no_refund_band(retriever, tickets):
    agent, _ = _agent(retriever, tickets, "Yes, you can change it. (Source: Cancellation and refund policy)")
    r = agent.respond("My bus leaves in 6 hours. Can I reschedule to next week?")
    assert r.text.startswith("No,") and "no refund" in r.text


def test_correct_no_is_kept(retriever, tickets):
    good = ("No, a date change is only allowed up to 24 hours before departure, so you can't change it now. "
            "If you cancel, 12 to 24 hours before departure gets a 50% refund. "
            "(Source: Cancellation and refund policy)")
    agent, _ = _agent(retriever, tickets, good)
    r = agent.respond("My bus leaves in 12 hours. Can I change my travel date?")
    assert r.text == good and "policy_check_replaced" not in r.flags


@pytest.mark.parametrize("hours", [24, 30])
def test_allowed_yes_is_kept(retriever, tickets, hours):
    good = ("Yes, you can change your travel date once, for ₹100 plus any fare difference. "
            "(Source: Cancellation and refund policy)")
    agent, llm = _agent(retriever, tickets, good)
    word = "exactly 24" if hours == 24 else str(hours)
    r = agent.respond(f"My bus leaves in {word} hours. Can I change my travel date?")
    assert r.text == good and "policy_check_replaced" not in r.flags
    assert "CAN still make their one date change" in llm.calls[0]["messages"][-1]["text"]


def test_wrong_no_at_30_hours_is_replaced(retriever, tickets):
    agent, _ = _agent(retriever, tickets, "No, you can no longer change the date. (Source: Cancellation and refund policy)")
    r = agent.respond("My bus leaves in 30 hours. Can I change my travel date?")
    assert r.text.startswith("Yes, you can still change your travel date once") and "₹100" in r.text


def test_no_policy_note_without_hours(retriever, tickets):
    agent, llm = _agent(retriever, tickets, "Yes, once, up to 24 hours before departure, for a ₹100 fee plus any "
                                            "fare difference. (Source: FAQ: Can I change my travel date?)")
    agent.respond("Can I change my travel date?")
    assert "POLICY CHECK" not in llm.calls[0]["messages"][-1]["text"]
