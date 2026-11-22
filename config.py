"""Central configuration for the BusSafar support app.

Secrets are never hard-coded. They are read, in order, from:
1. Environment variables (a local ``.env`` file is loaded automatically).
2. Streamlit secrets (``.streamlit/secrets.toml`` locally, or the Secrets
   panel on Streamlit Community Cloud).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def get_secret(name: str, default: str | None = None) -> str | None:
    """Return a secret from the environment or Streamlit secrets."""
    value = os.getenv(name)
    if value:
        return value.strip()
    try:  # Streamlit is optional for scripts and tests.
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:  # noqa: BLE001 - no secrets file, or not running in Streamlit
        pass
    return default


# --- Paths ---------------------------------------------------------------
DATA_DIR = BASE_DIR / "data"
KNOWLEDGE_BASE_DIR = DATA_DIR / "knowledge_base"
HELP_DOCS_FILE = KNOWLEDGE_BASE_DIR / "bussafar_help.txt"
BOOKINGS_CSV = DATA_DIR / "bookings.csv"
INDEX_DIR = BASE_DIR / "index"
TICKETS_CSV = BASE_DIR / "tickets.csv"

# --- Models --------------------------------------------------------------
# Names can be overridden without code changes if your key lists other models.
CHAT_MODEL = get_secret("GEMINI_CHAT_MODEL", "gemini-3.5-flash-lite")
EMBED_MODEL = get_secret("GEMINI_EMBED_MODEL", "gemini-embedding-001")

# --- Retrieval -----------------------------------------------------------
COLLECTION_NAME = "bussafar_help"
TOP_K = 3
# Cosine distance (0 = identical). Chunks further away than this are treated
# as "not relevant" so Sahil falls back instead of guessing.
MAX_DISTANCE = float(get_secret("RAG_MAX_DISTANCE", "0.36") or 0.36)

# When Gemini is unavailable, a help-doc section is quoted only if it is this close.
FALLBACK_MAX_DISTANCE = float(get_secret("RAG_FALLBACK_MAX_DISTANCE", "0.30") or 0.30)

# --- Agent ---------------------------------------------------------------
BUSINESS_NAME = "BusSafar"
AGENT_NAME = "Sahil"
GREETING = "Hello, I'm Sahil from BusSafar. How can I help with your trip?"
FALLBACK_MESSAGE = (
    "I don't have that information. Would you like me to connect you to a person?"
)
INTRO_MESSAGE = (
    "I'm Sahil, BusSafar's support agent. I answer only from BusSafar's help docs, and when I "
    "can't answer something, I hand you off to a person on our support team. I can help with "
    "your bookings, tickets, cancellations, refunds, trips and safety."
)
APP_TAGLINE = (
    "Sahil, BusSafar's support agent. Answers only from our help docs and hands off to a "
    "human when it can't."
)
OFF_TOPIC_MESSAGE = (
    "I'm sorry, I can only help with BusSafar bus bookings, tickets, cancellations, refunds, "
    "trips and safety."
)
TICKET_REPLY_PROMISE = "A person from our support team will reply within 24 hours."
MAX_INPUT_CHARS = 1000
MAX_TOOL_ROUNDS = 4
MAX_FAILED_LOOKUPS = 5
MAX_PASSCODE_ATTEMPTS = 5
PASSCODE_LOCKOUT_SECONDS = 60

SUGGESTED_QUESTIONS = [
    "How do I book a bus ticket?",
    "What ID proof should I carry?",
    "What is the cancellation policy?",
    "How can I check my refund?",
    "Can I change my journey date?",
    "What happens if my bus is delayed?",
    "How much luggage can I carry?",
]


def google_api_key() -> str | None:
    return get_secret("GOOGLE_API_KEY")


def app_passcode() -> str | None:
    return get_secret("APP_PASSCODE")
