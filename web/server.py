"""BusSafar website: a thin page whose main interaction is Sahil, the floating support chat.

Run locally:  uvicorn web.server:app --port 8000      then open http://localhost:8000

The page itself is static (web/static). This server adds four small endpoints:
  GET  /api/health    what is configured (never the values)
  GET  /api/help      BusSafar's own FAQs and policies, parsed from the help docs Sahil uses
  POST /api/session   passcode -> chat session id (keeps the Gemini quota private)
  POST /api/chat      one message to Sahil; the same agent code as the Streamlit app
"""
from __future__ import annotations

import hashlib
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
import re

from booking.booking_lookup import get_booking_status, normalize_pnr
from support.ticket_manager import CATEGORIES, TicketError, TicketManager, _normalize_ticket_id

log = logging.getLogger("bussafar.web")
STATIC = Path(__file__).resolve().parent / "static"

SESSION_TTL = 30 * 60          # idle chat sessions expire after 30 minutes
SESSION_MAX_AGE = 2 * 60 * 60  # a session ID stops working 2 hours after the passcode was entered
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


def _signing_key() -> bytes:
    secret = (config.app_passcode() or "") + "|" + (config.google_api_key() or "") + "|bussafar-session"
    return hashlib.sha256(secret.encode()).digest()


def sign(token: str) -> str:
    return token + "." + hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()[:32]


def verify(sid: str) -> bool:
    token, _, mac = (sid or "").rpartition(".")
    if not token or not hmac.compare_digest(sign(token), sid):
        return False
    # The token carries the time it was issued, so an old ID can't skip the passcode forever,
    # even on a fresh serverless instance that has never seen it.
    _, _, issued = token.rpartition("~")
    return issued.isdigit() and 0 <= time.time() - int(issued) <= SESSION_MAX_AGE


class SessionStore:
    """Chat sessions: one Sahil (with its own memory and ticket rules) per visitor chat.

    Session IDs are signed. On serverless hosts a request can land on a fresh instance that has
    never seen the chat; a validly signed ID then gets a new Sahil, primed with the recent turns
    the browser sends, instead of an error.
    """

    def __init__(self, factory) -> None:
        self.factory = factory
        self._items: OrderedDict[str, tuple[Sahil, float, int]] = OrderedDict()
        self._lock = threading.Lock()

    def create(self) -> str:
        sid = sign(f"{secrets.token_urlsafe(24)}~{int(time.time())}")
        with self._lock:
            self._prune()
            self._items[sid] = (self.factory(), time.time(), 0)
        return sid

    def take_turn(self, sid: str, history: list[dict] | None = None) -> Sahil | None:
        if not verify(sid):
            return None
        with self._lock:
            self._prune()
            item = self._items.get(sid)
            if item is None:
                agent = self.factory()
                agent.history = _clean_history(history)
                item = (agent, time.time(), len(agent.history) // 2)
                self._items[sid] = item
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


def _clean_history(history: list[dict] | None) -> list[dict]:
    """Recent turns sent back by the browser, trimmed and limited to plain text."""
    out = []
    for m in (history or [])[-12:]:
        role = "model" if m.get("role") in ("model", "bot", "assistant") else "user"
        text = str(m.get("text", ""))[: config.MAX_INPUT_CHARS]
        if text:
            out.append({"role": role, "text": text})
    return out


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
    categories = [{"id": k, "label": v} for k, v in CATEGORIES.items() if k not in ("unresolved_query",)]
    return {"topics": [t for t, _ in TOPICS], "faqs": faqs, "policies": policies, "ticket_categories": categories}


# Which in-chat form to show after a reply, so the customer fills fields instead of typing them.
FORM_FOR_FLAG = {"booking_need_pnr": "booking", "booking_need_phone": "booking_phone", "ticket_need_id": "ticket_status",
                 "person_offered": "contact"}


def default_agent_factory():
    """Same wiring as app.py: Gemini + Chroma when a key is set, code-only paths otherwise."""
    tickets = TicketManager(config.TICKETS_CSV)
    retriever = llm = None
    if config.google_api_key():
        from agent.sahil import GeminiLLM
        from rag.embeddings import GeminiEmbedder
        from rag.file_store import ensure_any_index
        from rag.retrieval import Retriever

        embedder = GeminiEmbedder()
        shared = {"retriever": Retriever(ensure_any_index(embedder), embedder)}
        return lambda: Sahil(shared["retriever"], GeminiLLM(), tickets, config.BOOKINGS_CSV)
    return lambda: Sahil(retriever, llm, tickets, config.BOOKINGS_CSV)


class UnlockIn(BaseModel):
    passcode: str = ""


class TicketIn(BaseModel):
    session: str
    category: str = "other"
    description: str = ""
    pnr: str = ""
    last4: str = ""


class TicketCheckIn(BaseModel):
    session: str
    ticket_id: str = ""


PNR_RE = re.compile(r"^BS\d{6}$")
TICKET_RE = re.compile(r"^TKT-\d{4,6}$")


class ChatIn(BaseModel):
    session: str
    message: str
    history: list[dict] = []


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
            "default-src 'self'; img-src 'self' data:; style-src 'self' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        return resp

    @app.get("/api/health")
    def health():
        return {"ok": True, "chat_model": config.CHAT_MODEL, "gemini_key_set": bool(config.google_api_key()),
                "passcode_required": True, "passcode_set": bool(config.app_passcode())}

    @app.get("/api/help")
    def help_docs():
        return help_payload()

    @app.post("/api/session")
    def unlock(body: UnlockIn, request: Request):
        who = visitor(request)
        limiter.check("wrong_passcode", who, UNLOCK_LIMIT, record=False)
        expected = config.app_passcode()
        if not expected:  # fail closed: no passcode configured means nobody gets in
            log.warning('{"event":"passcode_not_configured"}')
            raise HTTPException(503, "The chat is locked because its passcode is not set up yet.")
        if not hmac.compare_digest(body.passcode.encode(), expected.encode()):
            limiter.check("wrong_passcode", who, UNLOCK_LIMIT)
            raise HTTPException(401, "Wrong passcode.")
        limiter.check("session", who, SESSION_LIMIT)
        return {"session": sessions().create(), "greeting": config.GREETING}

    @app.post("/api/chat")
    def chat(body: ChatIn, request: Request):
        limiter.check("chat", visitor(request), CHAT_LIMIT)
        agent = sessions().take_turn(body.session, body.history)
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
        form = next((FORM_FOR_FLAG[f] for f in r.flags if f in FORM_FOR_FLAG), None)
        if form == "booking_phone":
            form = {"type": "booking", "pnr": agent.pending_pnr or ""}
        elif form:
            form = {"type": form}
        return {"text": r.text, "sources": r.sources, "ticket_ids": r.ticket_ids, "booking": booking, "form": form,
                "emergency": "emergency" in r.flags}

    @app.post("/api/ticket")
    def contact_person(body: TicketIn, request: Request):
        """The in-chat 'Contact a person' form. Same one-ticket-per-chat rule as the chat itself."""
        limiter.check("chat", visitor(request), CHAT_LIMIT)
        agent = sessions().take_turn(body.session)
        if agent is None:
            raise HTTPException(401, "Your chat expired. Please start a new chat.")
        desc = " ".join(body.description.split())[: config.MAX_INPUT_CHARS]
        if len(desc) < 5:
            raise HTTPException(400, "Please describe the issue before creating a ticket.")
        if body.category not in CATEGORIES or body.category == "unresolved_query":
            raise HTTPException(400, "Please choose an issue type.")
        category = body.category
        # PNR + last 4 phone digits are required (a safety report may skip them so nobody in danger
        # is held up) and must match a real booking, the same check as a booking lookup. The digits
        # stop anyone raising tickets on someone else's booking or probing which PNRs exist.
        pnr = normalize_pnr(body.pnr)
        if pnr or category != "safety":
            if not PNR_RE.match(pnr):
                raise HTTPException(400, "Please enter your PNR: BS followed by 6 digits, like BS123456.")
            if not re.fullmatch(r"\d{4}", body.last4.strip()):
                raise HTTPException(400, "Please enter the last 4 digits of the phone number on the booking.")
            if agent.failed_lookups >= config.MAX_FAILED_LOOKUPS:
                raise HTTPException(429, "Too many unsuccessful attempts in this chat. Please start a new chat later.")
            if not get_booking_status(pnr, body.last4.strip(), agent.bookings_path)["found"]:
                agent.failed_lookups += 1
                raise HTTPException(400, "I couldn't find a booking that matches that PNR and phone number.")
        existing = agent._open_session_ticket()
        if not existing and pnr and category != "safety":
            # One open ticket per booking too: reuse a ticket already open for this PNR.
            existing = next((t["ticket_id"] for t in agent.tickets.list_tickets()
                             if t.get("pnr") == pnr and t.get("status") != "Done"
                             and t.get("category") != "safety"), None)
            if existing:
                agent.session_tickets.setdefault(category, existing)
        if existing and category != "safety":
            text = (f"You already have an open ticket, {existing}, so I haven't created another one. "
                    f"{config.TICKET_REPLY_PROMISE}")
            agent._remember(f"[Contact form] {desc}", text)
            return {"text": text, "ticket_ids": [], "existing": existing}
        try:
            tid = agent.create_manual_ticket(desc, category, pnr or None)
        except TicketError as exc:
            raise HTTPException(400, str(exc))
        if category == "safety":
            agent.safety_ticket = agent.safety_ticket or tid
        else:
            agent.session_tickets.setdefault(category, tid)
        text = f"I've raised ticket {tid} for you. {config.TICKET_REPLY_PROMISE}"
        if category == "safety":
            text = "If you are in danger, please call 112 right away and use the SOS button in the app. " + text
        agent._remember(f"[Contact form] {desc}", text)
        log.info('{"event":"contact_form","category":"%s","ticket":"%s"}', category, tid)
        return {"text": text, "ticket_ids": [tid], "emergency": category == "safety"}

    @app.post("/api/ticket/check")
    def check_ticket(body: TicketCheckIn, request: Request):
        """The in-chat 'Track ticket' form: is this a real ticket ID? Says nothing else about it."""
        limiter.check("chat", visitor(request), CHAT_LIMIT)
        agent = sessions().take_turn(body.session)
        if agent is None:
            raise HTTPException(401, "Your chat expired. Please start a new chat.")
        tid = _normalize_ticket_id(body.ticket_id)
        if not TICKET_RE.match(tid):
            raise HTTPException(400, "Ticket IDs look like TKT-1001.")
        misses = getattr(agent, "failed_ticket_checks", 0)
        if misses >= config.MAX_FAILED_LOOKUPS:
            raise HTTPException(429, "Too many unknown ticket IDs in this chat. Please start a new chat later.")
        if agent.tickets.get_ticket(tid) is None:
            agent.failed_ticket_checks = misses + 1
            raise HTTPException(404, f"There's no ticket {tid}. Please check the ID on your ticket message.")
        return {"ticket_id": tid}

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")
    return app


app = create_app()
