"""Streamlit UI smoke tests using Streamlit's AppTest (no browser, no API key)."""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

import config

APP = str(config.BASE_DIR / "app.py")


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_PASSCODE", "test-pass")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    monkeypatch.setattr(config, "TICKETS_CSV", tmp_path / "tickets.csv")


def unlocked():
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_input[0].input("test-pass")
    at.button[0].click().run()
    return at


def test_wrong_passcode_stays_locked():
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_input[0].input("nope")
    at.button[0].click().run()
    assert any("Wrong passcode" in e.value for e in at.error)
    assert not at.chat_input


def test_missing_passcode_config_fails_closed(monkeypatch):
    monkeypatch.setenv("APP_PASSCODE", "")
    monkeypatch.setattr(config, "app_passcode", lambda: None)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert any("not configured" in e.value for e in at.error)


def test_right_passcode_shows_greeting_and_suggestions():
    at = unlocked()
    assert at.chat_message[0].markdown[0].value == config.GREETING
    labels = [b.label for b in at.button]
    for q in config.SUGGESTED_QUESTIONS:
        assert q in labels
    assert any("GOOGLE_API_KEY" in w.value for w in at.warning)


def test_sidebar_booking_lookup():
    at = unlocked()
    sb = at.sidebar
    sb.text_input[0].input("BS558954")
    sb.text_input[1].input("2087")
    next(b for b in sb.button if b.label == "Check status").click().run()
    assert any("Completed" in s.value for s in at.sidebar.success)


def test_sidebar_booking_lookup_wrong_phone():
    at = unlocked()
    at.sidebar.text_input[0].input("BS558954")
    at.sidebar.text_input[1].input("0000")
    next(b for b in at.sidebar.button if b.label == "Check status").click().run()
    assert any("couldn't find" in e.value for e in at.sidebar.error)
    assert not any("Gaurav" in s.value for s in at.sidebar.success)


def test_sidebar_ticket_creation_shows_id():
    at = unlocked()
    at.sidebar.text_area[0].input("I left my laptop bag on the bus.")
    next(b for b in at.sidebar.button if b.label == "Create ticket").click().run()
    assert any("TKT-1001" in s.value for s in at.sidebar.success)
