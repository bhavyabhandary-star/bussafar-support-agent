"""BusSafar website API: help content is BusSafar's own, chat goes through Sahil, limits hold."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from agent.sahil import Sahil
from rag.document_loader import load_help_docs
from web import server


@pytest.fixture
def client(tickets, monkeypatch):
    monkeypatch.setattr(config, "app_passcode", lambda: "demo-pass")
    app = server.create_app(lambda: Sahil(None, None, tickets, config.BOOKINGS_CSV))
    return TestClient(app)


def unlock(client):
    r = client.post("/api/session", json={"passcode": "demo-pass"})
    assert r.status_code == 200
    return r.json()["session"]


def test_page_and_assets_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Ask Sahil" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    for a in ("site.css", "site.js", "sahil.js"):
        assert client.get(f"/assets/{a}").status_code == 200


def test_help_is_bussafar_help_docs_word_for_word(client):
    d = client.get("/api/help").json()
    chunks = {c.chunk_id: c for c in load_help_docs(config.HELP_DOCS_FILE)}
    assert len(d["faqs"]) == 20 and len(d["policies"]) == 5
    for f in d["faqs"]:
        assert chunks[f["id"]].text == f"Q: {f['q']}\nA: {f['a']}" and f["topic"] in d["topics"]
    refund = next(p for p in d["policies"] if p["title"] == "Cancellation and refund policy")
    assert refund["lines"][:3] == ["More than 24 hours before departure: 90% refund.",
                                   "12 to 24 hours before departure: 50% refund.",
                                   "Less than 12 hours before departure: no refund."]


def test_page_has_no_hard_coded_policy_numbers():
    # The page reads refund slabs from /api/help, so it can never drift from the help docs.
    html = (server.STATIC / "index.html").read_text(encoding="utf-8")
    js = (server.STATIC / "assets" / "site.js").read_text(encoding="utf-8")
    for figure in ("90%", "50%", "₹100", "5 to 7", "15 kg"):
        assert figure not in html and figure not in js


def test_wrong_passcode_is_refused_and_limited(client):
    for _ in range(5):
        assert client.post("/api/session", json={"passcode": "nope"}).status_code == 401
    assert client.post("/api/session", json={"passcode": "nope"}).status_code == 429


def test_chat_needs_a_session(client):
    assert client.post("/api/chat", json={"session": "made-up", "message": "hi"}).status_code == 401


def test_booking_lookup_through_chat_hides_phone_digits(client):
    sid = unlock(client)
    r = client.post("/api/chat", json={"session": sid, "message": "PNR BS600101, phone ends in 3391"}).json()
    assert r["booking"]["status"] == "Pending" and "awaiting payment" in r["booking"]["status_meaning"]
    assert "phone_last4" not in r["booking"] and "3391" not in str(r)


def test_emergency_flag_and_single_ticket(client):
    sid = unlock(client)
    r = client.post("/api/chat", json={"session": sid, "message": "There has been an accident, please help!"}).json()
    assert r["emergency"] and "112" in r["text"] and r["ticket_ids"] == ["TKT-1001"]
    r = client.post("/api/chat", json={"session": sid, "message": "I want to speak to a person."}).json()
    assert r["ticket_ids"] == ["TKT-1002"]
    r = client.post("/api/chat", json={"session": sid, "message": "I left my bag on the bus."}).json()
    assert r["ticket_ids"] == [] and "TKT-1002" in r["text"]  # one ticket per chat


def test_chat_turn_cap(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_TURNS", 2)
    sid = unlock(client)
    for _ in range(2):
        assert client.post("/api/chat", json={"session": sid, "message": "thanks"}).status_code == 200
    assert client.post("/api/chat", json={"session": sid, "message": "thanks"}).status_code == 429


def test_sessions_are_separate(client):
    a, b = unlock(client), unlock(client)
    client.post("/api/chat", json={"session": a, "message": "I want to speak to a person."})
    r = client.post("/api/chat", json={"session": b, "message": "I want to speak to a person."}).json()
    assert r["ticket_ids"] == ["TKT-1002"]  # chat B is not blocked by chat A's ticket
