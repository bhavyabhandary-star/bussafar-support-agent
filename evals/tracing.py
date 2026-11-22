"""Record what Sahil did on each turn without changing Sahil.

The retriever and the model are wrapped, so a trace shows which help-doc sections were
retrieved (with distances), every model call (tool calls requested, time taken) and the
final response (text, sources, flags, tickets, booking). Phone digits are masked.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict
from typing import Any

_LAST4_KEYS = {"phone_last4", "last4"}


def mask(value: Any) -> Any:
    """Mask phone digits anywhere they could appear in a trace."""
    if isinstance(value, dict):
        return {k: ("****" if k in _LAST4_KEYS or "phone" in k else mask(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(phone(?:\s+(?:ends?|ending))?(?:\s+(?:in|with))?[\s:]*)(\d{4})\b", r"\1****", value,
                       flags=re.IGNORECASE)
        if re.search(r"\bBS\s?\d{6}\b", value, re.IGNORECASE):
            # a message with a PNR: any other stand-alone 4-digit number is treated as phone digits
            value = re.sub(r"(?<![\d₹.,])\b\d{4}\b(?![\d,])", "****", value)
        return value
    return value


class TracingRetriever:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls: list[dict] = []

    def search(self, question: str, k: int | None = None):
        started = time.perf_counter()
        chunks = self.inner.search(question, k) if k else self.inner.search(question)
        self.calls.append({"query": mask(question), "ms": round((time.perf_counter() - started) * 1000),
                           "chunks": [{"source": c.source, "id": c.chunk_id, "distance": round(c.distance, 4)}
                                      for c in chunks]})
        return chunks

    def search_all(self, question: str, k: int | None = None):
        return self.inner.search_all(question, k)


class TracingLLM:
    def __init__(self, inner, throttle=None) -> None:
        self.inner = inner
        self.throttle = throttle
        self.calls: list[dict] = []

    def generate(self, system, messages, tools):
        if self.throttle:
            self.throttle()
        started = time.perf_counter()
        error = None
        try:
            reply = self.inner.generate(system, messages, tools)
            return reply
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            entry = {"ms": round((time.perf_counter() - started) * 1000)}
            if error:
                entry["error"] = error
            else:
                entry["tool_calls"] = [{"name": c.name, "args": mask(dict(c.args))} for c in reply.function_calls]
                entry["text_chars"] = len(reply.text or "")
            self.calls.append(entry)


def response_record(resp) -> dict:
    d = asdict(resp)
    d["booking"] = mask(d["booking"]) if d.get("booking") else None
    return d
