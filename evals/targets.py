"""Where scenarios run: in-process (the real agent, its own temporary tickets file) or a live website.

Both return the same per-turn record, so the scorers don't care which one ran.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parents[1]
CODE_DIRS = ["agent", "rag", "booking", "support"]


def code_fingerprint() -> str:
    """Changes whenever the agent's code, prompts, help docs or bookings change (invalidates the cache)."""
    h = hashlib.sha256()
    files = sorted(p for d in CODE_DIRS for p in (ROOT / d).rglob("*.py")) + [
        ROOT / "config.py", config.HELP_DOCS_FILE, config.BOOKINGS_CSV]
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def fill(text: str, last4: dict[str, str], first_ticket: str | None) -> str:
    text = re.sub(r"\{last4:(BS\d{6})\}", lambda m: last4[m.group(1)], text)
    return text.replace("{first_ticket}", first_ticket or "TKT-0000")


class Throttle:
    """Keeps Gemini calls under the free tier's per-minute limit."""

    def __init__(self, gap: float) -> None:
        self.gap, self.last = gap, 0.0

    def wait(self) -> None:
        delay = self.gap - (time.monotonic() - self.last)
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()


class CountingLLM:
    def __init__(self, inner, throttle: Throttle) -> None:
        self.inner, self.throttle, self.calls = inner, throttle, 0

    def generate(self, system, messages, tools):
        self.throttle.wait()
        self.calls += 1
        return self.inner.generate(system, messages, tools)


class LocalTarget:
    """The agent in this process. Without Gemini, only code paths answer; the rest are skipped."""

    kind = "local"

    def __init__(self, live: bool, gap: float = 5.0) -> None:
        self.llm = self.retriever = None
        if live and config.google_api_key():
            from agent.sahil import GeminiLLM
            from rag.embeddings import GeminiEmbedder
            from rag.file_store import ensure_any_index
            from rag.retrieval import Retriever

            emb = GeminiEmbedder()
            self.retriever = Retriever(ensure_any_index(emb), emb)
            self.llm = CountingLLM(GeminiLLM(), Throttle(gap))
        self.model = config.CHAT_MODEL if self.llm else "offline"
        self.tmp = Path(tempfile.mkdtemp(prefix="sahil-evals-"))

    @property
    def calls(self) -> int:
        return self.llm.calls if self.llm else 0

    def run(self, sid: str, turns: list[str], last4: dict[str, str]) -> list[dict]:
        from agent.sahil import Sahil
        from support.ticket_manager import TicketManager

        tickets = TicketManager(self.tmp / f"{sid}.csv")
        agent = Sahil(self.retriever, self.llm, tickets, config.BOOKINGS_CSV)
        out, first_ticket = [], None
        for raw in turns:
            msg = fill(raw, last4, first_ticket)
            start = time.monotonic()
            r = agent.respond(msg)
            ms = int((time.monotonic() - start) * 1000)
            first_ticket = first_ticket or (r.ticket_ids[0] if r.ticket_ids else None)
            meta = {tid: {k: (tickets.get_ticket(tid) or {}).get(k) for k in ("category", "priority")}
                    for tid in r.ticket_ids}
            booking = None
            if r.booking:
                booking = {k: r.booking.get(k) for k in ("pnr", "passenger_name", "status", "amount_inr")}
            out.append({"user": msg, "text": r.text, "sources": r.sources, "ticket_ids": r.ticket_ids,
                        "booking": booking, "flags": r.flags, "emergency": "emergency" in r.flags,
                        "ms": ms, "tickets": meta})
        return out


class HttpTarget:
    """A running website (local uvicorn, Render or Vercel). Needs the chat passcode in EVAL_PASSCODE."""

    kind = "http"

    def __init__(self, url: str, passcode: str, gap: float = 5.0) -> None:
        import httpx

        self.url = url.rstrip("/")
        self.passcode = passcode
        self.http = httpx.Client(timeout=120)
        self.throttle = Throttle(gap)
        health = self.http.get(self.url + "/api/health").json()
        self.model = health.get("chat_model", "?") if health.get("gemini_key_set") else "offline"
        self.calls = 0

    def run(self, sid: str, turns: list[str], last4: dict[str, str]) -> list[dict]:
        r = self.http.post(self.url + "/api/session", json={"passcode": self.passcode})
        if r.status_code != 200:
            raise RuntimeError(f"could not start a chat: HTTP {r.status_code} {r.text[:120]}")
        session = r.json()["session"]
        out, first_ticket, history = [], None, []
        for raw in turns:
            msg = fill(raw, last4, first_ticket)
            self.throttle.wait()
            start = time.monotonic()
            resp = self.http.post(self.url + "/api/chat", json={"session": session, "message": msg, "history": history})
            ms = int((time.monotonic() - start) * 1000)
            self.calls += 1
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
            d = resp.json()
            first_ticket = first_ticket or (d["ticket_ids"][0] if d.get("ticket_ids") else None)
            history += [{"role": "user", "text": msg}, {"role": "model", "text": d["text"]}]
            out.append({"user": msg, "text": d["text"], "sources": d.get("sources", []),
                        "ticket_ids": d.get("ticket_ids", []), "booking": d.get("booking"), "flags": [],
                        "emergency": bool(d.get("emergency")), "ms": ms, "tickets": {}})
        return out


class Cache:
    """Recorded replies, keyed by target, model, code fingerprint and the scenario's turns."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    @staticmethod
    def key(*parts) -> str:
        return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]

    def get(self, key: str):
        return self.data.get(key)

    def put(self, key: str, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
