"""Sahil: the BusSafar conversational support agent.

Flow for every customer message:
1. Input validation and deterministic guardrails (emergency, prompt injection,
   other passengers' data). These never depend on the model.
2. Retrieval: the top 3 help-doc chunks are fetched BEFORE the model is called
   and passed in as the only allowed knowledge.
3. Gemini generates the answer and may call two tools: get_booking_status and
   create_ticket. Both are plain Python; the model never sees booking data it
   was not given by a verified lookup and never invents ticket IDs.
4. Output guardrails: redact PII, drop invented ticket IDs and sources, block
   system-prompt leaks.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import config
from agent import guardrails
from agent.prompts import SYSTEM_PROMPT, build_user_turn
from booking.booking_lookup import format_booking
from booking.booking_lookup import get_booking_status as lookup_booking
from rag.retrieval import RetrievedChunk, format_context
from support.ticket_manager import CATEGORIES, TicketError, TicketManager

log = logging.getLogger(__name__)

TOOL_DECLARATIONS = [
    {
        "name": "get_booking_status",
        "description": (
            "Look up ONE booking. Requires the PNR and the last 4 digits of the registered "
            "phone number, both provided by the customer. Returns details only if both match."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pnr": {"type": "string", "description": "Booking PNR, e.g. BS123456"},
                "phone_last4": {"type": "string", "description": "Last 4 digits of the registered phone number"},
            },
            "required": ["pnr", "phone_last4"],
        },
    },
    {
        "name": "create_ticket",
        "description": "Create a human support ticket. Returns the ticket_id on success.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_message": {"type": "string", "description": "The customer's issue in their words"},
                "reason": {"type": "string", "description": "Short reason a person must handle this"},
                "category": {"type": "string", "enum": list(CATEGORIES)},
                "priority": {"type": "string", "enum": ["normal", "high"]},
                "pnr": {"type": "string", "description": "PNR if the customer gave one, else empty"},
                "conversation_summary": {"type": "string", "description": "One or two sentence summary of the conversation"},
            },
            "required": ["customer_message", "reason", "category", "priority"],
        },
    },
]


# --- Model abstraction (lets tests run without network) ------------------
@dataclass
class FunctionCall:
    name: str
    args: dict


@dataclass
class LLMReply:
    text: str = ""
    function_calls: list[FunctionCall] = field(default_factory=list)
    raw: Any = None  # provider-specific content, replayed verbatim within a turn


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def generate(self, system: str, messages: list[dict], tools: list[dict]) -> LLMReply: ...


class GeminiLLM:
    """Gemini chat model via the google-genai SDK, with manual function calling."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        from google import genai

        api_key = api_key or config.google_api_key()
        if not api_key:
            raise LLMError("GOOGLE_API_KEY is not set.")
        self._client = genai.Client(api_key=api_key)
        self.model = model or config.CHAT_MODEL

    def _contents(self, messages: list[dict]):
        from google.genai import types

        contents = []
        for m in messages:
            if m.get("raw") is not None:
                contents.append(m["raw"])
            elif m["role"] == "tool":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(name=m["name"], response=m["response"])],
                    )
                )
            else:
                role = "model" if m["role"] == "model" else "user"
                contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m["text"])]))
        return contents

    def generate(self, system: str, messages: list[dict], tools: list[dict]) -> LLMReply:
        from google.genai import errors, types

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            max_output_tokens=600,
            tools=[types.Tool(function_declarations=tools)] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            resp = self._client.models.generate_content(
                model=self.model, contents=self._contents(messages), config=cfg
            )
        except errors.APIError as exc:
            raise LLMError(f"Gemini API error {getattr(exc, 'code', '')}: {exc}") from exc
        calls = [FunctionCall(fc.name, dict(fc.args or {})) for fc in (resp.function_calls or [])]
        raw = resp.candidates[0].content if resp.candidates else None
        text = ""
        if raw is not None and raw.parts:
            text = "".join(p.text for p in raw.parts if getattr(p, "text", None) and not getattr(p, "thought", False))
        return LLMReply(text=text.strip(), function_calls=calls, raw=raw)


# --- Agent ---------------------------------------------------------------
@dataclass
class SahilResponse:
    text: str
    sources: list[str] = field(default_factory=list)
    ticket_ids: list[str] = field(default_factory=list)
    booking: dict | None = None
    flags: list[str] = field(default_factory=list)


_PNR_IN_TEXT = re.compile(r"\b(BS\s?\d{6})\b", re.IGNORECASE)
_LAST4_IN_TEXT = re.compile(r"(?<![\dA-Za-z])(\d{4})(?!\d)")
_BOOKING_INTENT = re.compile(r"\b(booking|pnr|status|ticket|check|phone|ends?|last)\b", re.IGNORECASE)


def grounded_fallback(chunks: list[RetrievedChunk]) -> str | None:
    """Best-matching help-doc section, word for word, for when the model is down."""
    if not chunks:
        return None
    top = chunks[0]
    if top.distance > config.FALLBACK_MAX_DISTANCE:
        return None
    lines = top.text.splitlines()
    if top.kind == "faq":
        body = next((ln[2:].strip() for ln in lines if ln.startswith("A:")), "")
    else:
        body = " ".join(lines[1:])
    if not body:
        return None
    return f"{body} (Source: {top.source})"


class Sahil:
    def __init__(self, retriever, llm: LLM | None, ticket_manager: TicketManager,
                 bookings_path=config.BOOKINGS_CSV, history_turns: int = 6) -> None:
        self.retriever = retriever
        self.llm = llm
        self.tickets = ticket_manager
        self.bookings_path = bookings_path
        self.history_turns = history_turns
        self.history: list[dict] = []  # [{"role": "user"|"model", "text": str}]
        self.failed_lookups = 0
        self.ticket_ids: set[str] = set()
        self.safety_ticket: str | None = None

    # -- helpers --------------------------------------------------------
    def _remember(self, user: str, reply: str) -> None:
        self.history.append({"role": "user", "text": user})
        self.history.append({"role": "model", "text": reply})
        self.history = self.history[-2 * self.history_turns :]

    def _summary(self, latest: str) -> str:
        lines = [f"{'Customer' if m['role'] == 'user' else 'Sahil'}: {m['text'][:200]}" for m in self.history[-6:]]
        lines.append(f"Customer: {latest[:300]}")
        return guardrails.redact_pii(" | ".join(lines))

    def _retrieve(self, message: str) -> list[RetrievedChunk]:
        if self.retriever is None:
            return []
        query = message
        last_user = next((m["text"] for m in reversed(self.history) if m["role"] == "user"), "")
        if len(message.split()) <= 6 and last_user:  # short follow-ups lean on the previous question
            query = f"{last_user}\n{message}"
        try:
            return self.retriever.search(query)
        except Exception as exc:  # noqa: BLE001
            log.warning("Retrieval failed: %s", exc)
            return []

    def _tool_booking(self, args: dict) -> dict:
        if self.failed_lookups >= config.MAX_FAILED_LOOKUPS:
            return {"found": False, "message": "Too many unsuccessful attempts. Please contact support."}
        result = lookup_booking(args.get("pnr", ""), args.get("phone_last4", ""), self.bookings_path)
        if not result["found"]:
            self.failed_lookups += 1
            return {"found": False, "message": result["message"]}
        return {"found": True, "booking": result["booking"]}

    def _tool_ticket(self, args: dict, message: str) -> dict:
        try:
            t = self.tickets.create_ticket(
                customer_query=args.get("customer_message") or message,
                category=args.get("category", "other"),
                reason=args.get("reason", ""),
                priority=args.get("priority", "normal"),
                pnr=args.get("pnr"),
                conversation_summary=args.get("conversation_summary") or self._summary(message),
            )
        except TicketError as exc:
            return {"success": False, "error": str(exc)}
        self.ticket_ids.add(t.ticket_id)
        return {"success": True, "ticket_id": t.ticket_id, "priority": t.priority}

    def _direct_booking_lookup(self, msg: str) -> SahilResponse | None:
        """Handle "PNR + last 4 digits" messages in code, without the model."""
        pnr_m = _PNR_IN_TEXT.search(msg)
        if not pnr_m:
            return None
        rest = msg[: pnr_m.start()] + " " + msg[pnr_m.end():]
        digits = _LAST4_IN_TEXT.findall(rest)
        if len(digits) != 1:
            if not digits and _BOOKING_INTENT.search(msg):
                return SahilResponse(
                    "I can check that for you. Please share the last 4 digits of the phone "
                    "number registered on this booking.", flags=["booking_need_phone"])
            return None
        if self.failed_lookups >= config.MAX_FAILED_LOOKUPS:
            return SahilResponse(
                "I'm sorry, there have been too many unsuccessful attempts, so I can't check "
                "bookings in this chat. Please raise a support ticket and a person will help you.",
                flags=["booking_locked"])
        result = lookup_booking(pnr_m.group(1), digits[0], self.bookings_path)
        if not result["found"]:
            if result["error"] == "not_found":
                self.failed_lookups += 1
            return SahilResponse(result["message"], flags=["booking_not_found"])
        b = result["booking"]
        text = f"Here are your booking details. {format_booking(b)}"
        if b["status"].lower() == "cancelled":
            text += (" If you have a problem with this cancellation or its refund, I can raise a "
                     "ticket for a person to look into it.")
        return SahilResponse(text, booking=b, flags=["booking_found"])

    # -- public API -----------------------------------------------------
    def create_manual_ticket(self, description: str, category: str, pnr: str | None = None) -> str:
        """Ticket raised from the UI form. Raises TicketError on failure."""
        t = self.tickets.create_ticket(
            customer_query=description, category=category, reason="Raised from the support form",
            pnr=pnr, conversation_summary=self._summary(description),
        )
        self.ticket_ids.add(t.ticket_id)
        return t.ticket_id

    def respond(self, message: str) -> SahilResponse:
        check = guardrails.validate_input(message)
        if not check.ok:
            return SahilResponse(check.error or "Please type your question.", flags=["invalid_input"])
        msg = check.text

        # Deterministic guardrails first.
        if guardrails.detect_emergency(msg):
            ticket_id = self.safety_ticket
            new = []
            if ticket_id is None:
                try:
                    t = self.tickets.create_ticket(
                        customer_query=msg, category="safety", reason="Safety problem reported during a trip",
                        priority="high", conversation_summary=self._summary(msg),
                    )
                    ticket_id = self.safety_ticket = t.ticket_id
                    self.ticket_ids.add(ticket_id)
                    new = [ticket_id]
                except TicketError as exc:
                    log.error("Safety ticket failed: %s", exc)
            reply = guardrails.emergency_reply(ticket_id)
            self._remember(msg, reply)
            return SahilResponse(reply, ["Delays and operator cancellations"], new, flags=["emergency"])

        if guardrails.detect_prompt_injection(msg):
            self._remember(msg, guardrails.INJECTION_REPLY)
            return SahilResponse(guardrails.INJECTION_REPLY, flags=["prompt_injection"])

        if guardrails.detect_other_passenger_request(msg):
            self._remember(msg, guardrails.OTHER_PASSENGER_REPLY)
            return SahilResponse(guardrails.OTHER_PASSENGER_REPLY, flags=["other_passenger"])

        if guardrails.detect_intro_request(msg):
            self._remember(msg, config.INTRO_MESSAGE)
            return SahilResponse(config.INTRO_MESSAGE, flags=["intro"])

        direct = self._direct_booking_lookup(msg)
        if direct is not None:
            self._remember(msg, direct.text)
            return direct

        if self.llm is None:
            return SahilResponse(
                "Sorry, I'm not available right now because the AI service is not configured. "
                "You can still check your booking or raise a support ticket from the sidebar.",
                flags=["llm_unavailable"],
            )

        chunks = self._retrieve(msg)
        allowed_sources = {c.source for c in chunks}
        turn_messages = list(self.history) + [{"role": "user", "text": build_user_turn(msg, format_context(chunks))}]
        new_tickets: list[str] = []
        booking: dict | None = None
        reply_text = ""
        try:
            for _ in range(config.MAX_TOOL_ROUNDS):
                reply = self.llm.generate(SYSTEM_PROMPT, turn_messages, TOOL_DECLARATIONS)
                if not reply.function_calls:
                    reply_text = reply.text
                    break
                turn_messages.append({"role": "model", "text": reply.text, "raw": reply.raw,
                                      "function_calls": reply.function_calls})
                for call in reply.function_calls:
                    if call.name == "get_booking_status":
                        result = self._tool_booking(call.args)
                        if result.get("found"):
                            booking = result["booking"]
                    elif call.name == "create_ticket":
                        result = self._tool_ticket(call.args, msg)
                        if result.get("success"):
                            new_tickets.append(result["ticket_id"])
                    else:
                        result = {"error": f"Unknown tool {call.name}"}
                    turn_messages.append({"role": "tool", "name": call.name, "response": result})
            else:
                reply_text = ""
        except LLMError as exc:
            log.error("LLM failure: %s", exc)
            text = "Sorry, I'm having trouble answering right now. Please try again in a minute."
            if "429" in str(exc):
                text = "Sorry, I'm getting a lot of requests right now. Please try again in a minute."
            handoff = guardrails.detect_handoff(msg) if not new_tickets else None
            if handoff:
                result = self._tool_ticket({"customer_message": msg, "category": handoff,
                                            "reason": "Raised automatically while the AI service was unavailable",
                                            "priority": "normal"}, msg)
                if result.get("success"):
                    tid = result["ticket_id"]
                    text = (f"I'm sorry about this. I've raised ticket {tid} so a person from our team "
                            f"can help. {config.TICKET_REPLY_PROMISE}")
                    self._remember(msg, text)
                    return SahilResponse(text, ticket_ids=[tid], flags=["llm_error", "auto_ticket"])
            grounded = grounded_fallback(chunks) if not new_tickets else None
            if grounded:
                text = f"Here's what our help docs say: {grounded}"
                self._remember(msg, text)
                return SahilResponse(text, sources=[chunks[0].source], flags=["llm_error", "doc_fallback"])
            if new_tickets:
                text += f" Your ticket {', '.join(new_tickets)} was created. {config.TICKET_REPLY_PROMISE}"
            return SahilResponse(text, ticket_ids=new_tickets, flags=["llm_error"])

        if not reply_text:
            reply_text = config.FALLBACK_MESSAGE
        clean = guardrails.check_output(reply_text, allowed_sources, self.ticket_ids, SYSTEM_PROMPT)
        # If a ticket was created but the model forgot to mention it, say so ourselves.
        for tid in new_tickets:
            if tid not in clean:
                clean += f" Your ticket ID is {tid}. {config.TICKET_REPLY_PROMISE}"
        if not clean.strip():
            clean = config.FALLBACK_MESSAGE
        cited = [s for s in allowed_sources if s.lower() in clean.lower()]
        self._remember(msg, clean)
        return SahilResponse(clean, sources=cited, ticket_ids=new_tickets, booking=booking)
