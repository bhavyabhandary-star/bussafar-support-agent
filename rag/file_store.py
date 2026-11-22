"""A tiny in-memory vector store with the same query() shape as a Chroma collection.

Used where Chroma is too big to install (Vercel's serverless size limit). It embeds the 25
help-doc chunks once per server start with the same Gemini embedder, keeps the vectors in
memory (cached in /tmp between warm starts), and does exact cosine top-k in Python.
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import config
from rag.document_loader import file_fingerprint, load_help_docs
from rag.embeddings import Embedder

CACHE = Path(tempfile.gettempdir()) / "bussafar_help_vectors.json"


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return 1.0 - dot / (na * nb)


class FileCollection:
    def __init__(self, ids, documents, metadatas, embeddings, fingerprint: str = "") -> None:
        self.ids, self.documents, self.metadatas, self.embeddings = ids, documents, metadatas, embeddings
        self.metadata = {"fingerprint": fingerprint, "hnsw:space": "cosine"}

    def count(self) -> int:
        return len(self.ids)

    def query(self, query_embeddings, n_results: int = 3, include=None) -> dict:
        q = query_embeddings[0]
        ranked = sorted(range(len(self.ids)), key=lambda i: _cosine_distance(q, self.embeddings[i]))[:n_results]
        return {"ids": [[self.ids[i] for i in ranked]], "documents": [[self.documents[i] for i in ranked]],
                "metadatas": [[self.metadatas[i] for i in ranked]],
                "distances": [[_cosine_distance(q, self.embeddings[i]) for i in ranked]]}


def ensure_file_index(embedder: Embedder, help_file: Path = config.HELP_DOCS_FILE,
                      cache: Path = CACHE) -> FileCollection:
    fingerprint = f"{file_fingerprint(help_file)}:{config.EMBED_MODEL}"
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("fingerprint") == fingerprint:
            return FileCollection(saved["ids"], saved["documents"], saved["metadatas"], saved["embeddings"], fingerprint)
    except (OSError, ValueError, KeyError):
        pass
    chunks = load_help_docs(help_file)
    vectors = embedder.embed_documents([c.text for c in chunks])
    col = FileCollection([c.chunk_id for c in chunks], [c.text for c in chunks],
                         [{"kind": c.kind, "title": c.title, "source": c.source} for c in chunks], vectors, fingerprint)
    try:
        cache.write_text(json.dumps({"fingerprint": fingerprint, "ids": col.ids, "documents": col.documents,
                                     "metadatas": col.metadatas, "embeddings": col.embeddings}), encoding="utf-8")
    except OSError:
        pass
    return col


def ensure_any_index(embedder: Embedder):
    """Chroma when it is installed (laptop, Render); the in-memory store otherwise (Vercel)."""
    try:
        from rag.vector_store import ensure_index
    except ImportError:
        return ensure_file_index(embedder)
    return ensure_index(embedder)
