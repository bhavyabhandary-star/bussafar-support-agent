"""Shared test fixtures. Offline tests use a deterministic fake embedder and a
scripted fake LLM, so they run without network access or an API key."""
from __future__ import annotations

import hashlib
import math
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from agent.sahil import FunctionCall, LLMReply  # noqa: E402

_STOP = set("a an the i my me is are do does can how what which when if to of for in on and or you your with be it".split())


class FakeEmbedder:
    """Bag-of-words hashing embedder: similar wording -> similar vectors."""

    dim = 512

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        words = [w for w in re.findall(r"[a-z0-9₹]+", text.lower()) if w not in _STOP]
        for w in words:
            h = int(hashlib.md5(w.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


class FakeLLM:
    """Returns scripted replies in order and records what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def generate(self, system, messages, tools):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        if not self.replies:
            return LLMReply(text="")
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def text(t: str) -> LLMReply:
    return LLMReply(text=t)


def call(name: str, **args) -> LLMReply:
    return LLMReply(function_calls=[FunctionCall(name, args)])


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()


@pytest.fixture
def index_dir(tmp_path):
    d = tmp_path / "index"
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def retriever(fake_embedder, index_dir):
    from rag.retrieval import Retriever
    from rag.vector_store import ensure_index

    col = ensure_index(fake_embedder, config.HELP_DOCS_FILE, index_dir, "test_help")
    return Retriever(col, fake_embedder, top_k=3, max_distance=0.75)


@pytest.fixture
def tickets(tmp_path):
    from support.ticket_manager import TicketManager

    return TicketManager(tmp_path / "tickets.csv")
