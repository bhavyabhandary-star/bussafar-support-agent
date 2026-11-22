"""Deterministic guardrails that run before and after the language model.

These checks do not rely on the model behaving well: they catch emergencies,
prompt-injection attempts, requests for other passengers' data, and leaks of
phone numbers, keys or invented ticket IDs in the model's reply.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import config

# --- Input validation ----------------------------------------------------
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")


@dataclass
class InputCheck:
    ok: bool
    text: str
    error: str | None = None


def validate_input(text: object, max_chars: int = config.MAX_INPUT_CHARS) -> InputCheck:
    if not isinstance(text, str):
        return InputCheck(False, "", "Please type your question as text.")
    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = _CONTROL_CHARS.sub("", cleaned).strip()
    if not cleaned:
        return InputCheck(False, "", "Please type your question.")
    if len(cleaned) > max_chars:
        return InputCheck(
            False, cleaned[:max_chars],
            f"Your message is too long. Please keep it under {max_chars} characters.",
        )
    return InputCheck(True, cleaned)


# --- Prompt injection ----------------------------------------------------
_INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override|bypass|skip)\b.{0,40}\b(rules?|instructions?|guidelines?|prompts?|polic(y|ies)|restrictions?|guardrails?|programming)\b",
    r"\b(system|developer|hidden|initial|original)\s+(prompt|message|instructions?)\b",
    r"\b(reveal|show|print|repeat|display|tell me|give me|what (is|are))\b.{0,30}\b(your )?(prompt|instructions|system message|configuration|config)\b",
    r"\b(api[\s_-]?keys?|secret keys?|access tokens?|\.env|environment variables?|app passcode|your passcode)\b",
    r"\byou are now\b|\bnew instructions\b|\bfrom now on you\b",
    r"\b(jailbreak|DAN mode|developer mode|god mode|sudo mode)\b",
    r"\b(pretend|act|behave|roleplay)\b.{0,20}\b(as|like|to be)\b.{0,30}\b(admin|developer|unrestricted|different|another|evil|hacker|system)\b",
    r"\b(free ticket|free tickets|free ride|100% refund no matter|refund anyway)\b",
    r"</?\s*(system|assistant|instructions?)\s*>",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in _INJECTION_PATTERNS), re.IGNORECASE)


def detect_prompt_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text or ""))


# --- Emergencies / safety ------------------------------------------------
_EMERGENCY_PATTERNS = [
    r"\b(driv(er|ing)|bus)\b.{0,40}\b(danger\w*|rash\w*|reckless\w*|drunk|speeding|overspeed\w*|unsafe|crazy|fast)\b",
    r"\b(danger\w*|rash\w*|reckless\w*|drunk|unsafe)\b.{0,40}\b(driv(er|ing)|bus)\b",
    r"\b(accident|crash(ed)?|collision|fire|smoke|injur(ed|y)|bleeding|unconscious|heart attack|medical emergency)\b",
    r"\b(harass\w*|molest\w*|assault\w*|groped?|stalk\w*|threaten\w*|attack\w*|abuse\w*|touch(ed|ing) me|misbehav\w*)\b",
    r"\b(emergency|help me(?!\s+(?:to\s+)?(?:book|find|with|cancel|change|check|choose|get|know|understand|track|plan|pick|select|reschedule|pay|apply|use|share|see|print|download|resend))|i am not safe|i'?m not safe|i feel unsafe|in danger|scared for my life|sos)\b",
    r"\b(stranded|abandoned)\b.{0,40}\b(night|alone|highway|road)\b",
]
_EMERGENCY_RE = re.compile("|".join(f"(?:{p})" for p in _EMERGENCY_PATTERNS), re.IGNORECASE)
# Questions *about* safety features are not emergencies.
_SAFETY_INFO_RE = re.compile(
    r"^\s*(is there|are there|do you have|what (is|are)|how (do|does|can)|tell me about)\b.{0,60}\b(safety|sos)\b",
    re.IGNORECASE,
)


def detect_emergency(text: str) -> bool:
    text = text or ""
    if _SAFETY_INFO_RE.search(text):
        return False
    return bool(_EMERGENCY_RE.search(text))


# --- Requests for other passengers' data ---------------------------------
_OTHER_PASSENGER_PATTERNS = [
    r"\bwho\b.{0,30}\b(sitting|seated|is in|booked|travell?ing)\b",
    r"\b(seat|berth)\s*(no\.?|number)?\s*\w{1,4}\b.{0,30}\b(who|name|phone|number|details)\b",
    r"\b(name|phone|number|contact|details|seat)s?\b.{0,20}\b(of|for)\b.{0,20}\b(other|another|fellow|co-?)\s*(passengers?|travell?ers?|customers?)\b",
    r"\b(passenger|customer|traveller|booking)\s+(list|manifest|database|records|data)\b",
    r"\b(all|every|other|list)\b.{0,20}\b(bookings|pnrs|passengers|customers)\b",
    r"\b(status|booking|ticket|seat|phone|number|details)\b.{0,15}\bof\s+[A-Z][a-z]+\s+[A-Z][a-z]+",
    r"\b[A-Z][a-z]+\s+[A-Z][a-z]+'s\s+(booking|ticket|seat|phone|number|pnr|details)\b",
]
_OTHER_PASSENGER_PATTERNS_EXTRA = [
    r"\b(all|every|other)\b.{0,15}\b(passenger|customer|traveller)s?\b.{0,10}\b(names?|details|phones?|numbers?|seats?|bookings?)\b",
    r"\b(another|other|fellow)\s+(passenger|customer|traveller)'?s?\b.{0,20}\b(name|phone|number|seat|details|booking|pnr)",
    r"\b(passenger|person|traveller|lady|man|woman|guy)\s+(next to|beside|near|behind|in front of|sitting (next to|near|beside))\b",
]
_OTHER_PASSENGER_RE = re.compile("|".join(f"(?:{p})" for p in _OTHER_PASSENGER_PATTERNS))
_OTHER_PASSENGER_RE_I = re.compile("|".join(f"(?:{p})" for p in _OTHER_PASSENGER_PATTERNS[:5]), re.IGNORECASE)


_OTHER_PASSENGER_EXTRA_RE = re.compile("|".join(f"(?:{p})" for p in _OTHER_PASSENGER_PATTERNS_EXTRA), re.IGNORECASE)


def detect_other_passenger_request(text: str) -> bool:
    text = text or ""
    return bool(_OTHER_PASSENGER_RE_I.search(text) or _OTHER_PASSENGER_RE.search(text)
                or _OTHER_PASSENGER_EXTRA_RE.search(text))


# --- PII redaction -------------------------------------------------------
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_LONG_DIGITS_RE = re.compile(r"(?<!\d)\d{9,}(?!\d)")
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_AADHAAR_RE = re.compile(r"(?<!\d)\d{4}[\s-]\d{4}[\s-]\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}|sk-[A-Za-z0-9]{20,}")


_LAST4_MENTION_RE = re.compile(
    r"((?:phone|mobile|number|ends?(?:\s+(?:in|with))?|ending(?:\s+(?:in|with))?|last\s*(?:4|four)(?:\s+digits)?)\D{0,12})(\d{4})(?!\d)",
    re.IGNORECASE,
)


def redact_pii(text: str) -> str:
    text = text or ""
    text = _LAST4_MENTION_RE.sub(lambda m: m.group(1) + "****", text)
    text = _API_KEY_RE.sub("[redacted key]", text)
    text = _EMAIL_RE.sub("[redacted email]", text)
    text = _AADHAAR_RE.sub("[redacted ID number]", text)
    text = _CARD_RE.sub("[redacted number]", text)
    text = _PHONE_RE.sub("[redacted phone]", text)
    text = _LONG_DIGITS_RE.sub("[redacted number]", text)
    return text


# --- Output checks -------------------------------------------------------
_TICKET_ID_RE = re.compile(r"\bTKT-\d+\b")
_SOURCE_RE = re.compile(r"\(\s*Source:\s*([^)]+?)\s*\)", re.IGNORECASE)


def check_output(
    reply: str,
    allowed_sources: set[str],
    valid_ticket_ids: set[str],
    system_prompt: str = "",
) -> str:
    """Clean the model's reply before showing it to the customer."""
    reply = (reply or "").strip()

    # 1. Never leak the system prompt.
    if system_prompt and _leaks_prompt(reply, system_prompt):
        return config.OFF_TOPIC_MESSAGE

    # 2. Never show phone numbers, keys or other identifiers.
    reply = redact_pii(reply)

    # 3. Never mention a ticket ID that was not actually created.
    def _ticket(m: re.Match) -> str:
        return m.group(0) if m.group(0) in valid_ticket_ids else "[no ticket created]"

    reply = _TICKET_ID_RE.sub(_ticket, reply)

    # 4. Only cite sources that were actually retrieved this turn.
    allowed_lower = {s.lower() for s in allowed_sources}

    def _source(m: re.Match) -> str:
        cited = [s.strip() for s in re.split(r";|\s\|\s", m.group(1))]
        kept = [s for s in cited if s.lower() in allowed_lower]
        return f"(Source: {'; '.join(kept)})" if kept else ""

    reply = _SOURCE_RE.sub(_source, reply).strip()
    return re.sub(r"[ \t]{2,}", " ", reply)


_ALLOWED_PROMPT_PHRASES = [
    "bookings, tickets, cancellations, refunds, trips and safety",
    "bus bookings, tickets, cancellations, refunds, trips and safety",
    "an app for booking intercity bus tickets with many bus operators across India",
]


def _leaks_prompt(reply: str, system_prompt: str, window: int = 80) -> bool:
    # Customer-facing phrases quoted in the prompt are allowed in replies.
    for allowed in (config.FALLBACK_MESSAGE, config.TICKET_REPLY_PROMISE, config.GREETING,
                    *_ALLOWED_PROMPT_PHRASES):
        system_prompt = system_prompt.replace(allowed, " ")
    norm_reply = " ".join(reply.lower().split())
    norm_prompt = " ".join(system_prompt.lower().split())
    for start in range(0, max(1, len(norm_prompt) - window), window // 2):
        piece = norm_prompt[start : start + window]
        if len(piece) == window and piece in norm_reply:
            return True
    return False


# --- Simple intents handled without the model -----------------------------
_INTRO_RE = re.compile(
    r"^\s*(what (do|can) you do|who are you|what are you|what is bussafar|what('s| is) bussafar|"
    r"tell me about (yourself|bussafar)|what does bussafar do|introduce yourself)\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_HANDOFF_PATTERNS = [
    ("lost_item", r"\b(left|lost|forgot|forgotten|misplaced)\b.{0,40}\b(on|in)\b.{0,10}\bbus\b"),
    ("bus_no_show", r"\bbus\b.{0,40}\b(never (came|arrived|showed( up)?|turned up)|didn'?t (come|arrive|show|turn up)|did not (come|arrive|show|turn up)|(left|departed|went) (early|before time|without (me|us))|no[- ]show)"),
    ("human_request", r"\b(talk|speak|connect|chat)\b.{0,20}\b(to|with)\b.{0,10}\b(a |an |real )?(person|human|agent|someone|representative|executive|staff)\b|\b(want|need) (a|an|to reach a) (human|person|agent)\b"),
    ("complaint", r"\b(complain\w*|furious|angry|worst service|disgusted|fed up|unacceptable)\b"),
]


# Hand-offs that are always enforced in code (the brief's rules), before the model.
ALWAYS_ESCALATE = {"lost_item", "bus_no_show", "human_request"}

_AMOUNT_RE = re.compile(r"(?:₹|rs\.?|inr|rupees?)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:₹|rs\.?|inr|rupees?)\b", re.IGNORECASE)
_REFUND_RE = re.compile(r"\brefund", re.IGNORECASE)
REFUND_ESCALATION_LIMIT = 2000


def requested_refund_amount(text: str) -> float | None:
    """The rupee amount in a refund request, if the message names one."""
    if not _REFUND_RE.search(text or ""):
        return None
    amounts = []
    for m in _AMOUNT_RE.finditer(text):
        raw = (m.group(1) or m.group(2) or "").replace(",", "")
        try:
            amounts.append(float(raw))
        except ValueError:
            pass
    return max(amounts) if amounts else None


def detect_intro_request(text: str) -> bool:
    return bool(_INTRO_RE.search(text or ""))


def detect_handoff(text: str) -> str | None:
    """Category for messages that clearly need a person (used when the model is down)."""
    for category, pattern in _HANDOFF_PATTERNS:
        if re.search(pattern, text or "", re.IGNORECASE):
            return category
    return None


# --- Canned safe replies -------------------------------------------------
INJECTION_REPLY = (
    "I'm sorry, I can't do that. I can only help with BusSafar bookings, tickets, "
    "cancellations, refunds, trips and safety, and I follow BusSafar's policies for everyone."
)
OTHER_PASSENGER_REPLY = (
    "I'm sorry, I can't share any details about other passengers, such as names, seats "
    "or phone numbers. If you'd like to check your own booking, please share your PNR "
    "and the last 4 digits of your registered phone number."
)


def emergency_reply(ticket_id: str | None) -> str:
    base = (
        "I'm sorry you're going through this. If you are in danger, please call 112 right "
        "away and use the SOS button in the BusSafar app."
    )
    if ticket_id:
        return (
            f"{base} I've raised a high-priority ticket ({ticket_id}) for our safety team. "
            f"{config.TICKET_REPLY_PROMISE} (Source: Delays and operator cancellations)"
        )
    return (
        f"{base} I couldn't create a support ticket just now, so please also use the "
        "SOS button and try again shortly. (Source: Delays and operator cancellations)"
    )
