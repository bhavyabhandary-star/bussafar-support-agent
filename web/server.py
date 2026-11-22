"""BusSafar website: a thin page whose main interaction is Sahil, the floating support chat.

Run locally:  uvicorn web.server:app --port 8000      then open http://localhost:8000

The page itself is static (web/static). This server adds four small endpoints:
  GET  /api/health    what is configured (never the values)
  GET  /api/help      BusSafar's own FAQs and policies, parsed from the help docs Sahil uses
  POST /api/session   passcode -> chat session id (keeps the Gemini quota private)
  POST /api/chat      one message to Sahil; the same agent code as the Streamlit app
"""
from __future__ import annotations

import hmac
import logging
import secrets
import threading
import time
from collections import OrderedDict, defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from agent.sahil import Sahil
from rag.document_loader import load_help_docs
from support.ticket_manager import TicketManager

log = logging.getLogger("bussafar.web")
STATIC = Path(__file__).resolve().parent / "static"

SESSION_TTL = 30 * 60          # idle chat sessions expire after 30 minutes
MAX_SESSIONS = 500             # oldest sessions are dropped beyond this
MAX_TURNS = 40                 # messages per chat session
CHAT_LIMIT = (30, 600)         # 30 messages per visitor per 10 minutes
UNLOCK_LIMIT = (5, 300)        # 5 wrong passcodes per visitor per 5 minutes
SESSION_LIMIT = (20, 300)      # 20 new chats per visitor per 5 minutes

# Help Centre groups. Assignment is by question wording; the text itself is never changed.
TOPICS = [
    ("Booking & payment", ("book", "payment", "seat", "sleeper", "discount", "sms", "print")),
    ("ID & boarding", ("id proof", "boarding", "someone else")),
    ("Cancellation & refunds", ("cancel", "refund")),
    ("Date & name changes", ("change",)),
    ("Delays", ("late", "operator")),
    ("Luggage & pets", ("luggage", "pets")),
    ("Safety", ("safety", "women")),
]


class SessionStore:
    """In-memory chat sessions: one Sahil (with its own memory and ticket rules) per visitor chat."""

    def __init__(self, factory) -> None:
        self.factory = factory
        self._items: OrderedDict[str, tuple[Sahil, float, int]] = OrderedDict()
        self._lock = threading.Lock()

    def create(self) -> str:
        sid = secrets.token_urlsafe(24)
        with self._lock:
            self._prune()
            self._items[sid] = (self.factory(), time.time(), 0)
        return sid

    def take_turn(self, sid: str) -> Sahil | None:
        with self._lock:
            self._prune()
            item = self._items.get(sid)
            if item is None:
                return None
            agent, _, turns = item
            if turns >= MAX_TURNS:
                raise HTTPException(429, "This chat has reached its message limit. Please start a new chat.")
            self._items[sid] = (agent, time.time(), turns + 1)
            self._items.move_to_end(sid)
            return agent

    def _prune(self) -> None:
        now = time.time()
        for sid in [s for s, (_, seen, _) in self._items.items() if now - seen > SESSION_TTL]:
            del self._items[sid]
        while len(self._items) >= MAX_SESSIONS:
            self._items.popitem(last=False)


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, bucket: str, key: str, limit: tuple[int, int], record: bool = True) -> None:
        count, window = limit
        now = time.time()
        with self._lock:
            q = self._hits[(bucket, key)]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= count:
                raise HTTPException(429, "Too many requests. Please wait a few minutes and try again.")
            if record:
                q.append(now)


def _topic(question: str) -> str:
    q = question.lower()
    # Most specific topics first, so "Can I book for someone else?" lands in ID & boarding.
    order = ["Safety", "ID & boarding", "Delays", "Date & name changes", "Luggage & pets",
             "Cancellation & refunds", "Booking & payment"]
    words = dict(TOPICS)
    return next((name for name in order if any(w in q for w in words[name])), "Booking & payment")


def help_payload() -> dict:
    """FAQs and policies exactly as written in the help docs (the source Sahil answers from)."""
    chunks = load_help_docs(config.HELP_DOCS_FILE)
    faqs = [{"id": c.chunk_id, "q": c.title, "a": c.text.split("\nA: ", 1)[1], "topic": _topic(c.title)}
            for c in chunks if c.kind == "faq"]
    policies = [{"id": c.chunk_id, "title": c.title, "lines": c.text.splitlines()[1:]}
                for c in chunks if c.kind == "policy"]
    return {"topics": [t for t, _ in TOPICS], "faqs": faqs, "policies": policies}


def default_agent_factory():
    """Same wiring as app.py: Gemini + Chroma when a key is set, code-only paths otherwise."""
    tickets = TicketManager(config.TICKETS_CSV)
    retriever = llm = None
    if config.google_api_key():
        from agent.sahil import GeminiLLM
        from rag.embeddings import GeminiEmbedder
        from rag.retrieval import Retriever
        from rag.vector_store import ensure_index

        embedder = GeminiEmbedder()
        shared = {"retriever": Retriever(ensure_index(embedder), embedder)}
        return lambda: Sahil(shared["retriever"], GeminiLLM(), tickets, config.BOOKINGS_CSV)
    return lambda: Sahil(retriever, llm, tickets, config.BOOKINGS_CSV)


class UnlockIn(BaseModel):
    passcode: str = ""


class ChatIn(BaseModel):
    session: str
    message: str


def create_app(agent_factory=None) -> FastAPI:
    app = FastAPI(title="BusSafar", docs_url=None, redoc_url=None, openapi_url=None)
    state: dict = {"sessions": SessionStore(agent_factory) if agent_factory else None}
    limiter = RateLimiter()

    def sessions() -> SessionStore:
        if state["sessions"] is None:  # built on first use so the page loads even if Gemini is slow
            state["sessions"] = SessionStore(default_agent_factory())
        return state["sessions"]

    def visitor(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        return (fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown"))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'")
        return resp

    @app.get("/api/health")
    def health():
        return {"ok": True, "chat_model": config.CHAT_MODEL, "gemini_key_set": bool(config.google_api_key()),
                "passcode_required": bool(config.app_passcode())}

    @app.get("/api/help")
    def help_docs():
        return help_payload()

    @app.post("/api/session")
    def unlock(body: UnlockIn, request: Request):
        who = visitor(request)
        limiter.check("wrong_passcode", who, UNLOCK_LIMIT, record=False)
        expected = config.app_passcode()
        if expected and not hmac.compare_digest(body.passcode.encode(), expected.encode()):
            limiter.check("wrong_passcode", who, UNLOCK_LIMIT)
            raise HTTPException(401, "Wrong passcode.")
        limiter.check("session", who, SESSION_LIMIT)
        return {"session": sessions().create(), "greeting": config.GREETING}

    @app.post("/api/chat")
    def chat(body: ChatIn, request: Request):
        limiter.check("chat", visitor(request), CHAT_LIMIT)
        agent = sessions().take_turn(body.session)
        if agent is None:
            raise HTTPException(401, "Your chat expired. Please start a new chat.")
        started = time.time()
        r = agent.respond(body.message)
        booking = None
        if r.booking:  # never send phone digits to the browser
            booking = {k: r.booking.get(k) for k in
                       ("pnr", "passenger_name", "route", "travel_date", "seat", "status", "status_meaning",
                        "amount_inr")}
        log.info('{"event":"chat_turn","ms":%d,"flags":%s,"tickets":%s,"sources":%d}',
                 (time.time() - started) * 1000, r.flags, r.ticket_ids, len(r.sources))
        return {"text": r.text, "sources": r.sources, "ticket_ids": r.ticket_ids, "booking": booking,
                "emergency": "emergency" in r.flags}

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")
    return app


app = create_app()
