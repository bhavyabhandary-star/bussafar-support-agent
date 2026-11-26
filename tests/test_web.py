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


def test_missing_passcode_setting_keeps_the_chat_locked(tickets, monkeypatch):
    monkeypatch.setattr(config, "app_passcode", lambda: None)
    c = TestClient(server.create_app(lambda: Sahil(None, None, tickets, config.BOOKINGS_CSV)))
    h = c.get("/api/health").json()
    assert h["passcode_required"] is True and h["passcode_set"] is False
    for code in ("", "anything"):
        assert c.post("/api/session", json={"passcode": code}).status_code == 503


def test_empty_passcode_is_refused(client):
    assert client.post("/api/session", json={"passcode": ""}).status_code == 401


def test_old_session_ids_stop_working(client, monkeypatch):
    sid = unlock(client)
    assert server.verify(sid)
    real = server.time.time
    monkeypatch.setattr(server.time, "time", lambda: real() + server.SESSION_MAX_AGE + 1)
    assert not server.verify(sid)
    assert not server.verify(server.sign("no-issue-time"))


def test_widget_never_saves_the_session_in_the_browser():
    js = (server.STATIC / "assets" / "sahil.js").read_text(encoding="utf-8")
    assert "sessionStorage.setItem" not in js and "localStorage" not in js
    assert "const needsPasscode = true" in js


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


def test_file_store_matches_chroma_shape(fake_embedder, tmp_path):
    from rag.file_store import ensure_file_index
    from rag.retrieval import Retriever
    col = ensure_file_index(fake_embedder, cache=tmp_path / "v.json")
    assert col.count() == 25
    r = Retriever(col, fake_embedder)
    assert r.search_all("Can I change my travel date?")[0].source == "FAQ: Can I change my travel date?"
    again = ensure_file_index(fake_embedder, cache=tmp_path / "v.json")  # served from the cache file
    assert again.ids == col.ids


def test_vercel_entry_point_serves_the_site():
    from api.index import app
    c = TestClient(app)
    assert c.get("/api/health").json()["ok"] and "Ask Sahil" in c.get("/").text


def test_signed_session_survives_a_fresh_instance(tickets, monkeypatch):
    monkeypatch.setattr(config, "app_passcode", lambda: "demo-pass")
    factory = lambda: Sahil(None, None, tickets, config.BOOKINGS_CSV)  # noqa: E731
    sid = TestClient(server.create_app(factory)).post("/api/session", json={"passcode": "demo-pass"}).json()["session"]
    fresh = TestClient(server.create_app(factory))  # another serverless instance
    hist = [{"role": "user", "text": "I want to check my booking status"},
            {"role": "model", "text": "Please share your PNR."}]
    r = fresh.post("/api/chat", json={"session": sid, "message": "thanks", "history": hist})
    assert r.status_code == 200
    forged = sid[:-4] + "abcd"
    assert fresh.post("/api/chat", json={"session": forged, "message": "hi"}).status_code == 401


def test_help_lists_ticket_categories_for_the_contact_form(client):
    cats = {c["id"] for c in client.get("/api/help").json()["ticket_categories"]}
    assert {"safety", "refund_dispute", "other"} <= cats and "unresolved_query" not in cats


def test_chat_asks_for_the_booking_form(client):
    s = unlock(client)
    d = client.post("/api/chat", json={"session": s, "message": "I want to check my booking status"}).json()
    assert d["form"] == {"type": "booking"}
    d = client.post("/api/chat", json={"session": s, "message": "My PNR is BS558954"}).json()
    assert d["form"] == {"type": "booking", "pnr": "BS558954"}


def test_contact_form_creates_one_ticket_per_chat(client):
    s = unlock(client)
    assert client.post("/api/ticket", json={"session": s, "description": "hi"}).status_code == 400
    assert client.post("/api/ticket", json={"session": "bad", "description": "My bag is lost"}).status_code == 401
    d = client.post("/api/ticket", json={"session": s, "category": "lost_item",
                                         "description": "Left my bag on the bus", "pnr": "BS558954", "last4": "2087"}).json()
    assert len(d["ticket_ids"]) == 1 and d["ticket_ids"][0] in d["text"] and not d["emergency"]
    again = client.post("/api/ticket", json={"session": s, "category": "complaint",
                                             "description": "Still waiting for help", "pnr": "BS558954", "last4": "2087"}).json()
    assert again["ticket_ids"] == [] and again["existing"] == d["ticket_ids"][0]
    sos = client.post("/api/ticket", json={"session": s, "category": "safety",
                                           "description": "The driver is driving dangerously"}).json()
    assert sos["emergency"] and "112" in sos["text"] and len(sos["ticket_ids"]) == 1


def test_contact_form_needs_a_real_pnr(client):
    s = unlock(client)
    post = lambda **kw: client.post("/api/ticket", json={"session": s, "category": "lost_item",
                                                         "description": "Left my bag on the bus", **kw})
    r = post()
    assert r.status_code == 400 and "PNR" in r.json()["error"]
    assert post(pnr="12345").status_code == 400
    r = post(pnr="BS558954")
    assert r.status_code == 400 and "last 4 digits" in r.json()["error"]
    r = post(pnr="BS000000", last4="2087")
    assert r.status_code == 400 and "couldn't find a booking" in r.json()["error"]
    assert post(pnr="BS558954", last4="0000").status_code == 400     # real PNR, wrong digits
    assert client.post("/api/ticket", json={"session": s, "category": "made_up", "pnr": "BS558954",
                                            "last4": "2087", "description": "Left my bag"}).status_code == 400
    assert post(pnr="bs 558954", last4="2087").json()["ticket_ids"]   # tidied and accepted
    # a safety report is never held up by a missing PNR
    sos = client.post("/api/ticket", json={"session": s, "category": "safety", "description": "Driver is drunk"})
    assert sos.status_code == 200 and sos.json()["emergency"]


def test_contact_form_reuses_an_open_ticket_for_the_same_pnr(client):
    first = client.post("/api/ticket", json={"session": unlock(client), "category": "lost_item",
                                             "description": "Left my bag on the bus", "pnr": "BS558954", "last4": "2087"}).json()
    other_chat = client.post("/api/ticket", json={"session": unlock(client), "category": "complaint",
                                                  "description": "Nobody called me back", "pnr": "BS558954", "last4": "2087"}).json()
    assert other_chat["ticket_ids"] == [] and other_chat["existing"] == first["ticket_ids"][0]


def test_ticket_check_only_accepts_real_ticket_ids(client):
    s = unlock(client)
    tid = client.post("/api/ticket", json={"session": s, "category": "lost_item", "pnr": "BS558954", "last4": "2087",
                                           "description": "Left my bag on the bus"}).json()["ticket_ids"][0]
    assert client.post("/api/ticket/check", json={"session": s, "ticket_id": tid.lower()}).json() == {"ticket_id": tid}
    assert client.post("/api/ticket/check", json={"session": s, "ticket_id": "1001"}).json() == {"ticket_id": "TKT-1001"}
    r = client.post("/api/ticket/check", json={"session": s, "ticket_id": "TKT-9999"})
    assert r.status_code == 404 and "no ticket TKT-9999" in r.json()["error"]
    assert client.post("/api/ticket/check", json={"session": s, "ticket_id": "hello"}).status_code == 400
    assert client.post("/api/ticket/check", json={"session": "bad", "ticket_id": tid}).status_code == 401


def test_contact_form_locks_after_five_wrong_tries(client):
    s = unlock(client)
    for _ in range(5):
        assert client.post("/api/ticket", json={"session": s, "category": "lost_item", "pnr": "BS558954",
                                                "last4": "0000", "description": "Left my bag"}).status_code == 400
    r = client.post("/api/ticket", json={"session": s, "category": "lost_item", "pnr": "BS558954",
                                         "last4": "2087", "description": "Left my bag"})
    assert r.status_code == 429


def test_ticket_check_locks_after_five_unknown_ids(client):
    s = unlock(client)
    for n in range(5):
        assert client.post("/api/ticket/check", json={"session": s, "ticket_id": f"TKT-90{n}0"}).status_code == 404
    assert client.post("/api/ticket/check", json={"session": s, "ticket_id": "TKT-9100"}).status_code == 429


# --- Emergencies are never rate limited (demo bug: "There has been accident. Please help!" got
# "Too many requests") ------------------------------------------------------------------------
DEMO_EMERGENCY = "There has been accident. Please help!"


def _hit_chat_limit(client, monkeypatch, sid):
    monkeypatch.setattr(server, "CHAT_LIMIT", (2, 600))
    for _ in range(2):
        assert client.post("/api/chat", json={"session": sid, "message": "thanks"}).status_code == 200
    r = client.post("/api/chat", json={"session": sid, "message": "thanks"})
    assert r.status_code == 429
    return r


def test_emergency_is_answered_after_the_chat_limit(client, monkeypatch):
    sid = unlock(client)
    _hit_chat_limit(client, monkeypatch, sid)
    r = client.post("/api/chat", json={"session": sid, "message": DEMO_EMERGENCY})
    assert r.status_code == 200
    d = r.json()
    assert d["emergency"] and "112" in d["text"] and d["ticket_ids"] == ["TKT-1001"]
    assert "Too many" not in d["text"]


def test_emergency_is_answered_after_the_turn_cap(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_TURNS", 1)
    sid = unlock(client)
    assert client.post("/api/chat", json={"session": sid, "message": "thanks"}).status_code == 200
    assert client.post("/api/chat", json={"session": sid, "message": "thanks"}).status_code == 429
    d = client.post("/api/chat", json={"session": sid, "message": "The bus is on fire!"}).json()
    assert d["emergency"] and "112" in d["text"]


def test_emergency_past_its_own_limit_still_gets_112_but_no_new_tickets(client, monkeypatch):
    monkeypatch.setattr(server, "EMERGENCY_LIMIT", (1, 600))
    sid = unlock(client)
    first = client.post("/api/chat", json={"session": sid, "message": DEMO_EMERGENCY}).json()
    assert first["ticket_ids"] == ["TKT-1001"]
    for msg in ("Someone is harassing me", "A passenger is injured and bleeding"):
        r = client.post("/api/chat", json={"session": sid, "message": msg})
        assert r.status_code == 200
        d = r.json()
        assert d["emergency"] and "112" in d["text"] and d["ticket_ids"] == [] and "TKT-1001" in d["text"]
    other = unlock(client)  # a new chat from the same visitor past the limit: reply, no ticket spam
    d = client.post("/api/chat", json={"session": other, "message": DEMO_EMERGENCY}).json()
    assert d["emergency"] and "112" in d["text"] and d["ticket_ids"] == []


def test_emergency_still_needs_the_passcode(client):
    r = client.post("/api/chat", json={"session": "made-up", "message": DEMO_EMERGENCY})
    assert r.status_code == 401


def test_emergency_makes_no_model_call_even_when_gemini_is_out_of_quota(tickets, monkeypatch):
    from agent.sahil import LLMError

    class QuotaLLM:
        calls = 0

        def generate(self, *a, **k):
            QuotaLLM.calls += 1
            raise LLMError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(config, "app_passcode", lambda: "demo-pass")
    c = TestClient(server.create_app(lambda: Sahil(None, QuotaLLM(), tickets, config.BOOKINGS_CSV)))
    sid = unlock(c)
    d = c.post("/api/chat", json={"session": sid, "message": DEMO_EMERGENCY}).json()
    assert d["emergency"] and "112" in d["text"] and d["ticket_ids"] == ["TKT-1001"]
    assert QuotaLLM.calls == 0


def test_limit_reply_is_friendly_and_says_when_to_retry(client, monkeypatch):
    sid = unlock(client)
    r = _hit_chat_limit(client, monkeypatch, sid)
    msg = r.json()["error"]
    assert "Too many requests" not in msg and "Please wait about" in msg and "112" in msg
    assert int(r.headers["retry-after"]) > 0


def test_one_chat_cannot_burst(client, monkeypatch):
    monkeypatch.setattr(server, "CHAT_BURST_LIMIT", (3, 120))
    a, b = unlock(client), unlock(client)
    for _ in range(3):
        assert client.post("/api/chat", json={"session": a, "message": "thanks"}).status_code == 200
    assert client.post("/api/chat", json={"session": a, "message": "thanks"}).status_code == 429
    assert client.post("/api/chat", json={"session": b, "message": "thanks"}).status_code == 200


def test_global_cap_bounds_total_spend(client, monkeypatch):
    monkeypatch.setattr(server, "GLOBAL_CHAT_LIMIT", (2, 600))
    a, b = unlock(client), unlock(client)
    assert client.post("/api/chat", json={"session": a, "message": "thanks"}).status_code == 200
    assert client.post("/api/chat", json={"session": b, "message": "thanks"}).status_code == 200
    assert client.post("/api/chat", json={"session": b, "message": "thanks"}).status_code == 429
    assert client.post("/api/chat", json={"session": a, "message": DEMO_EMERGENCY}).status_code == 200


def test_limits_suit_a_demo_audience_on_one_wifi():
    # One venue IP, many people: the per-IP limit must allow a busy demo, and the cost caps must exist.
    assert server.CHAT_LIMIT[0] >= 100 and server.GLOBAL_CHAT_LIMIT[0] >= server.CHAT_LIMIT[0]
    assert server.CHAT_BURST_LIMIT[0] <= 30 and server.MAX_TURNS <= 100


def test_safety_contact_form_is_not_rate_limited(client, monkeypatch):
    sid = unlock(client)
    _hit_chat_limit(client, monkeypatch, sid)
    r = client.post("/api/ticket", json={"session": sid, "category": "safety", "description": "Bus accident on NH48"})
    assert r.status_code == 200 and "112" in r.json()["text"] and r.json()["ticket_ids"]
    r = client.post("/api/ticket", json={"session": sid, "category": "lost_item", "pnr": "BS558954",
                                         "last4": "2087", "description": "Left my bag"})
    assert r.status_code == 429
