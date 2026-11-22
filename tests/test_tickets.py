"""Human support ticket creation."""
from __future__ import annotations

import csv
import threading

import pytest

from support.ticket_manager import FIELDNAMES, TicketError, TicketManager


def test_ticket_has_all_fields(tickets):
    t = tickets.create_ticket("I left my laptop bag on the bus.", "lost_item", "Item left behind",
                              pnr="bs558954", conversation_summary="Customer lost a bag.")
    assert t.ticket_id == "TKT-1001"
    row = tickets.get_ticket("TKT-1001")
    assert set(row) == set(FIELDNAMES)
    assert row["status"] == "Pending" and row["pnr"] == "BS558954" and row["category"] == "lost_item"
    assert row["created_at"] and row["conversation_summary"] == "Customer lost a bag."


def test_ids_increment_and_never_repeat(tickets):
    ids = [tickets.create_ticket(f"issue {i}", "other").ticket_id for i in range(5)]
    assert ids == [f"TKT-{n}" for n in range(1001, 1006)]


def test_ids_unique_under_concurrency(tmp_path):
    tm = TicketManager(tmp_path / "t.csv")
    out = []
    threads = [threading.Thread(target=lambda: out.append(tm.create_ticket("x", "other").ticket_id)) for _ in range(15)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(out)) == 15


def test_safety_is_always_high_priority(tickets):
    assert tickets.create_ticket("Driver is rash", "safety", priority="normal").priority == "high"


def test_invalid_category_priority_and_pnr_are_sanitised(tickets):
    t = tickets.create_ticket("hello", "made_up", priority="urgent!!", pnr="DROP TABLE")
    assert (t.category, t.priority, t.pnr) == ("other", "normal", "")


def test_empty_query_raises(tickets):
    with pytest.raises(TicketError):
        tickets.create_ticket("   ", "other")


def test_pii_is_redacted_in_tickets(tickets):
    t = tickets.create_ticket("Call me on 9876543210 or mail a@b.com", "other")
    assert "9876543210" not in t.customer_query and "a@b.com" not in t.customer_query


def test_unwritable_store_raises_instead_of_claiming_success(tmp_path):
    d = tmp_path / "dir_not_file"
    d.mkdir()
    tm = TicketManager(d)  # path is a directory: writing must fail
    with pytest.raises(TicketError):
        tm.create_ticket("x", "other")


def test_list_open_tickets(tickets):
    tickets.create_ticket("a", "other")
    tickets.create_ticket("b", "complaint")
    assert [r["ticket_id"] for r in tickets.list_tickets("open")] == ["TKT-1001", "TKT-1002"]
    with open(tickets.path, newline="", encoding="utf-8") as f:
        assert next(csv.reader(f)) == FIELDNAMES
