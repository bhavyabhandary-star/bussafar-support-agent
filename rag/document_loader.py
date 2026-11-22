"""Load the BusSafar help docs and split them into one chunk per FAQ / policy.

The help docs mark each FAQ with ``Q:`` (answer on the following ``A:`` line)
and each policy section with ``Policy:``. No text is added or rewritten; each
chunk's text is copied verbatim from the source file.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

EXPECTED_FAQS = 20
EXPECTED_POLICIES = 5  # 3 from the original help docs + Booking statuses + Support ticket statuses

_POLICY_RE = re.compile(r"^Policy:\s*(?P<title>.+)$")
_Q_RE = re.compile(r"^Q:\s*(?P<q>.+)$")
_A_RE = re.compile(r"^A:\s*(?P<a>.+)$")
# Lines that end a policy section (headings that are not policy content).
_SECTION_HEADINGS = {"frequently asked questions", "policies"}


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    kind: str  # "faq" or "policy"
    title: str  # FAQ question or policy name
    text: str  # verbatim chunk text used for embedding and answering
    source: str  # human-readable source reference shown to the customer
    order: int  # position in the source document
    metadata: dict = field(default_factory=dict, compare=False, hash=False)


class HelpDocsFormatError(ValueError):
    """Raised when the help docs do not contain the expected sections."""


def file_fingerprint(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def parse_help_docs(text: str, source_file: str = "bussafar_help.txt") -> list[Chunk]:
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    chunks: list[Chunk] = []
    faq_n = 0
    pol_n = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        q = _Q_RE.match(line)
        p = _POLICY_RE.match(line)
        if q:
            question = q.group("q").strip()
            answer = ""
            j = i + 1
            while j < len(lines) and not lines[j]:
                j += 1
            if j < len(lines) and _A_RE.match(lines[j]):
                answer = _A_RE.match(lines[j]).group("a").strip()
                i = j
            if not answer:
                raise HelpDocsFormatError(f"FAQ without an answer: {question!r}")
            faq_n += 1
            chunks.append(
                Chunk(
                    chunk_id=f"faq-{faq_n:02d}",
                    kind="faq",
                    title=question,
                    text=f"Q: {question}\nA: {answer}",
                    source=f"FAQ: {question}",
                    order=len(chunks),
                    metadata={"source_file": source_file},
                )
            )
        elif p:
            title = p.group("title").strip()
            body: list[str] = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if _POLICY_RE.match(nxt) or _Q_RE.match(nxt):
                    break
                if nxt.lower() in _SECTION_HEADINGS:
                    break
                if nxt:
                    body.append(nxt)
                j += 1
            if not body:
                raise HelpDocsFormatError(f"Empty policy section: {title!r}")
            pol_n += 1
            # Policy names are shown in sentence case, e.g. "Cancellation and refund policy".
            chunks.append(
                Chunk(
                    chunk_id=f"policy-{pol_n:02d}",
                    kind="policy",
                    title=title,
                    text=f"Policy: {title}\n" + "\n".join(body),
                    source=title,
                    order=len(chunks),
                    metadata={"source_file": source_file},
                )
            )
            i = j - 1
        i += 1
    return chunks


def load_help_docs(path: Path, strict: bool = True) -> list[Chunk]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Help docs not found at {path}. Save the BusSafar help docs as plain text there."
        )
    chunks = parse_help_docs(path.read_text(encoding="utf-8"), source_file=path.name)
    faqs = sum(c.kind == "faq" for c in chunks)
    policies = sum(c.kind == "policy" for c in chunks)
    if strict and (faqs != EXPECTED_FAQS or policies != EXPECTED_POLICIES):
        raise HelpDocsFormatError(
            f"Expected {EXPECTED_FAQS} FAQs and {EXPECTED_POLICIES} policies, "
            f"found {faqs} FAQs and {policies} policies in {path.name}."
        )
    return chunks
