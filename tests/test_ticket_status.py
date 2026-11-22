"""Saved ticket statuses: Pending -> In progress -> Done, history, old-file upgrade, Sahil and staff tools."""
from __future__ import annotations

import csv

import pytest

import config
from agent.sahil import Sahil
from booking.booking_lookup import booking_status_meaning, format_booking, get_booking_status
from support.ticket_manager import STATUSES, TicketError, TicketManager, normalize_status
from tests.conftest import FakeLLM, call, text


def test_new_ticket_is_pending_with_history(tickets):
    t = tickets.create_ticket("I left my bag", "lost_item")
    row = tickets.get_ticket(t.ticket_id)
    assert row["status"] == "Pending" and row["updated_at"] == row["created_at"]
    assert [h["status"] for h in tickets.history(t.ticket_id)] == ["Pending"]


def test_status_lifecycle_and_history(tickets):
    tickets.create_ticket("I left my bag", "lost_item")
    assert tickets.update_status("TKT-1001", "in progress", "calling the operator")["status"] == "In progress"
    row = tickets.update_status("tkt-1001", "resolved", "bag returned", changed_by="Asha")
    assert row["status"] == "Done" and row["status_note"] == "bag returned"
    hist = tickets.history("1001")
    assert [h["status"] for h in hist] == ["Pending", "In progress", "Done"]
    assert hist[-1]["changed_by"] == "Asha"
    assert [r["ticket_id"] for r in tickets.list_tickets("Done")] == ["TKT-1001"]
    assert tickets.list_tickets("Pending") == []
    # survives a "restart": a brand-new manager reading the same file
    assert TicketManager(tickets.path).get_ticket("TKT-1001")["status"] == "Done"


def test_bad_status_or_ticket_is_rejected(tickets):
    tickets.create_ticket("x", "other")
    with pytest.raises(TicketError):
        tickets.update_status("TKT-1001", "lost")
    with pytest.raises(TicketError):
        tickets.update_status("TKT-9999", "Done")
    assert normalize_status("open") == "Pending" and STATUSES == ["Pending", "In progress", "Done"]


def test_old_tickets_csv_is_upgraded(tmp_path):
    path = tmp_path / "tickets.csv"
    old = ["ticket_id", "created_at", "customer_query", "category", "reason", "priority", "pnr",
           "conversation_summary", "status"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(old)
        w.writerow(["TKT-1006", "2026-11-21T14:00:00+00:00", "WTF", "complaint", "upset", "normal", "", "", "open"])
    tm = TicketManager(path)
    assert tm.get_ticket("TKT-1006")["status"] == "Pending"
    assert tm.create_ticket("new issue", "other").ticket_id == "TKT-1007"
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert "updated_at" in rows[0] and [r["status"] for r in rows] == ["Pending", "Pending"]


def test_sahil_checks_ticket_status_without_leaking_message(retriever, tickets):
    tickets.create_ticket("I left my laptop bag, my phone is 9876543210", "lost_item")
    tickets.update_status("TKT-1001", "In progress", "operator contacted")
    # 1) Answered in code (no model needed), never treated as a booking lookup.
    agent = Sahil(retriever, FakeLLM([]), tickets, config.BOOKINGS_CSV)
    r = agent.respond("What's the status of my ticket TKT-1001?")
    assert "TKT-1001 is In progress" in r.text and "operator contacted" in r.text
    assert "PNR" not in r.text and "9876543210" not in r.text
    assert "ticket ID" in agent.respond("What's the status of my support ticket?").text
    assert "couldn't find" in agent.respond("status of tkt-4242").text
    # 2) The model's tool returns only status fields.
    llm = FakeLLM([call("get_ticket_status", ticket_id="tkt-1001"), text("Your ticket TKT-1001 is In progress.")])
    agent2 = Sahil(retriever, llm, tickets, config.BOOKINGS_CSV)
    agent2.respond("hey, any idea about the bag I reported earlier?")
    tool_result = llm.calls[1]["messages"][-1]["response"]
    assert tool_result["status"] == "In progress" and "customer_query" not in tool_result
    assert "9876543210" not in str(tool_result)


def test_unknown_ticket_lookups_are_capped(retriever, tickets):
    agent = Sahil(retriever, FakeLLM([]), tickets, config.BOOKINGS_CSV)
    replies = [agent.respond(f"status of TKT-9{i:03d}").text for i in range(config.MAX_FAILED_LOOKUPS + 1)]
    assert all("couldn't find" in r for r in replies[:-1])
    assert "can't check more tickets" in replies[-1]


def test_resolved_ticket_allows_a_new_one(retriever, tickets):
    agent = Sahil(retriever, FakeLLM([]), tickets, config.BOOKINGS_CSV)
    first = agent.respond("I left my laptop bag on the bus")
    tid = first.ticket_ids[0]
    again = agent.respond("I left my laptop bag on the bus")
    assert again.ticket_ids == [] and tid in again.text
    tickets.update_status(tid, "Done")
    third = agent.respond("I left my laptop bag on the bus")
    assert third.ticket_ids and third.ticket_ids[0] != tid


def test_booking_status_meanings():
    assert "forfeiting" in booking_status_meaning("No-Show")
    assert "payment confirmation" in booking_status_meaning("hold")
    b = get_booking_status("BS600104", "6637")["booking"]
    assert b["status"] == "No-Show" and "refund" in b["status_meaning"]
    assert "completed the journey" in format_booking(get_booking_status("BS558954", "2087")["booking"])


def test_manage_tickets_cli(tickets, capsys):
    import manage_tickets

    tickets.create_ticket("bag", "lost_item")
    manage_tickets.main(["set", "TKT-1001", "Done", "returned"], tickets)
    manage_tickets.main(["history", "TKT-1001"], tickets)
    out = capsys.readouterr().out
    assert "TKT-1001 is now Done" in out and "returned" in out
