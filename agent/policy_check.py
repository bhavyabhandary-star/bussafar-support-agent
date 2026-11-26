"""Deterministic checks for policy rules that depend on the customer's numbers.

The help docs allow one date change "up to 24 hours before departure". The model sometimes
misreads that line, especially next to the refund bands in the same policy section
("12 to 24 hours before departure: 50% refund"), and tells a customer whose bus leaves in
12 hours that they can still change the date. Comparing 12 with 24 is arithmetic, so code
does it: the verdict is given to the model with the question, and a reply that contradicts
it is replaced with a reply built from the policy text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

POLICY_SOURCE = "Cancellation and refund policy"
DATE_CHANGE_CUTOFF_HOURS = 24  # "One date change is allowed up to 24 hours before departure"

_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
             "eighteen": 18, "twenty": 20, "twenty-four": 24, "thirty": 30, "forty-eight": 48}
_NUM = r"(\d+(?:\.\d+)?|" + "|".join(sorted(map(re.escape, _WORD_NUM), key=len, reverse=True)) + r")"
_QUAL = r"(less than|under|fewer than|below|within|more than|over|above|exactly|about|around|only|just)?\s*"
_DURATION = re.compile(rf"\b{_QUAL}{_NUM}\s*(hours?|hrs?|h|days?|minutes?|mins?)\b", re.IGNORECASE)

# What the hours refer to must be the departure, not a delay or a past change.
_DEPARTURE = re.compile(
    r"\b(?:leaves?|leaving|departs?|departing|departure|starts?|starting|board\w*|journey|travel\w*|trip|bus)\b",
    re.IGNORECASE)
_NOT_DEPARTURE = re.compile(r"\b(?:delay\w*|late|ago|waiting|waited|stuck)\b", re.IGNORECASE)

_DATE_CHANGE = re.compile(
    r"\b(?:reschedul\w*|postpone\w*|prepone\w*)\b"
    r"|\b(?:change|changing|move|moving|shift|shifting|modify|amend)\b[^.?!]{0,30}"
    r"\b(?:date|day|travel|journey|trip|ticket|booking|bus)\b"
    r"|\b(?:date|day)\s+change\b",
    re.IGNORECASE)
# A second change is refused by "One date change" whatever the hours; leave that to the docs.
_SECOND_CHANGE = re.compile(r"\b(?:again|second|twice|another)\b|\balready\s+changed\b", re.IGNORECASE)


def hours_to_departure(text: str) -> float | None:
    """Hours until departure that the customer states, or None if they don't state one.

    "less than 24 hours" counts as just under 24, "more than 24 hours" as just over.
    """
    if not text or _NOT_DEPARTURE.search(text) or not _DEPARTURE.search(text):
        return None
    m = _DURATION.search(text)
    if not m:
        return None
    qual, num, unit = (m.group(1) or "").lower(), m.group(2).lower(), m.group(3).lower()
    value = float(num) if num[0].isdigit() else float(_WORD_NUM[num])
    if unit.startswith("d"):
        value *= 24
    elif unit.startswith("m"):
        value /= 60
    if qual in ("less than", "under", "fewer than", "below", "within"):
        value -= 0.01
    elif qual in ("more than", "over", "above"):
        value += 0.01
    return value


def asks_date_change(text: str) -> bool:
    return bool(text and _DATE_CHANGE.search(text) and not _SECOND_CHANGE.search(text))


def _fmt(hours: float) -> str:
    h = round(hours)
    if abs(hours - h) > 0.001:  # "less than 24" / "more than 24": say it the customer's way
        return f"{'less' if hours < h else 'more'} than {h} hours"
    return f"{h} hour" if h == 1 else f"{h} hours"


def refund_band(hours: float) -> str:
    """The refund line of the policy that applies at this many hours before departure."""
    if hours > 24:
        return "More than 24 hours before departure: 90% refund"
    if hours >= 12:
        return "12 to 24 hours before departure: 50% refund"
    return "Less than 12 hours before departure: no refund"


@dataclass(frozen=True)
class DateChangeCheck:
    hours: float
    allowed: bool

    @property
    def band(self) -> str:
        return refund_band(self.hours)

    def note(self) -> str:
        """Verdict worked out by code, sent to the model with the customer's message."""
        when = _fmt(self.hours)
        if self.allowed:
            return (f"POLICY CHECK (worked out by BusSafar's system from the customer's message and the "
                    f"{POLICY_SOURCE}; it is correct): the bus leaves in {when}, which is at least 24 hours "
                    f"away, so the customer CAN still make their one date change, for ₹100 plus any fare "
                    f"difference. Start the reply with \"Yes,\".")
        return (f"POLICY CHECK (worked out by BusSafar's system from the customer's message and the "
                f"{POLICY_SOURCE}; it is correct): the bus leaves in {when}. One date change is allowed only "
                f"up to 24 hours before departure, so a date change is NOT available for this booking. Start "
                f"the reply with \"No,\" and say that a date change is allowed only up to 24 hours before "
                f"departure. If they cancel instead, this band applies: {self.band}. Offer to "
                f"connect them to a person from our support team.")

    def reply(self) -> str:
        """Reply built only from the policy text, used when the model contradicts the verdict."""
        when = _fmt(self.hours)
        if self.allowed:
            return (f"Yes, you can still change your travel date once, because your bus leaves in {when} and a "
                    f"date change is allowed up to 24 hours before departure. It costs ₹100 plus any fare "
                    f"difference. (Source: {POLICY_SOURCE})")
        band = self.band
        cancel = (f"you would get a 50% refund (12 to 24 hours before departure)" if "50%" in band else
                  "there is no refund (less than 12 hours before departure)" if "no refund" in band else
                  "you would get a 90% refund (more than 24 hours before departure)")
        return (f"No, a date change is no longer available for this trip. One date change is allowed only up "
                f"to 24 hours before departure, and your bus leaves in {when}. If you cancel now, {cancel}. "
                f"If you'd like, I can connect you to a person from our support team. (Source: {POLICY_SOURCE})")


def check_date_change(message: str, recent_user_turns: list[str] | None = None) -> DateChangeCheck | None:
    """Date-change verdict when the customer asks about a date change and says when the bus leaves.

    Follow-ups count: "My bus leaves in 12 hours." then "Can I change the date?", or the other way round.
    """
    recent = [t for t in (recent_user_turns or [])[-2:] if t][::-1]  # most recent first
    if asks_date_change(message):
        hours = hours_to_departure(message)
        for t in recent:
            if hours is not None:
                break
            hours = hours_to_departure(t)
    elif recent and asks_date_change(recent[0]) and len(message.split()) <= 12:
        hours = hours_to_departure(message)
    else:
        return None
    if hours is None or hours < 0:
        return None
    return DateChangeCheck(hours=hours, allowed=hours >= DATE_CHANGE_CUTOFF_HOURS)


_OPENS_YES = re.compile(r"^\W*(?:yes|yeah|sure|of course|absolutely|certainly|good news|great news|you can)\b",
                        re.IGNORECASE)
_OPENS_NO = re.compile(r"^\W*(?:no\b|unfortunately|sorry|i'?m sorry|i'?m afraid)", re.IGNORECASE)
_NEGATES = re.compile(r"\b(?:no|not|cannot|can'?t|can\s+not|isn'?t|unable|unavailable|no longer|won'?t)\b",
                      re.IGNORECASE)
_REFUSES_CHANGE = re.compile(
    r"\b(?:no longer|not (?:be )?(?:allowed|possible|available|eligible)|cannot|can'?t|unable to)\b",
    re.IGNORECASE)


_STATES_CUTOFF = re.compile(r"\b24[\s-]?(?:hours?|hrs?|hour)\b", re.IGNORECASE)


def contradicts(reply: str, check: DateChangeCheck) -> bool:
    """True when the model's reply gives the opposite verdict to the policy check."""
    text = (reply or "").strip()
    if not text:
        return True
    if check.allowed:
        return bool(_OPENS_NO.search(text) or (not _OPENS_YES.search(text) and _REFUSES_CHANGE.search(text)))
    # A refusal must also give the reason, the 24-hour rule; otherwise the customer only hears "No".
    return bool(_OPENS_YES.search(text) or not _NEGATES.search(text) or not _STATES_CUTOFF.search(text))
