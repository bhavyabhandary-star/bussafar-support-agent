"""Semantic search over the help docs."""
from __future__ import annotations

from dataclasses import dataclass

import config
from rag.embeddings import Embedder


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    kind: str
    title: str
    source: str
    text: str
    distance: float


class Retriever:
    def __init__(self, collection, embedder: Embedder, top_k: int = config.TOP_K,
                 max_distance: float = config.MAX_DISTANCE) -> None:
        self.collection = collection
        self.embedder = embedder
        self.top_k = top_k
        self.max_distance = max_distance

    def search_all(self, question: str, k: int | None = None) -> list[RetrievedChunk]:
        """Top-k chunks with distances, without the relevance filter."""
        question = (question or "").strip()
        if not question:
            return []
        vector = self.embedder.embed_query(question)
        res = self.collection.query(
            query_embeddings=[vector],
            n_results=k or self.top_k,
            include=["documents", "metadatas", "distances"],
        )
        out = []
        for cid, doc, meta, dist in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            out.append(
                RetrievedChunk(
                    chunk_id=cid,
                    kind=meta.get("kind", ""),
                    title=meta.get("title", ""),
                    source=meta.get("source", ""),
                    text=doc,
                    distance=float(dist),
                )
            )
        return out

    def search(self, question: str, k: int | None = None) -> list[RetrievedChunk]:
        """Top-k chunks that are close enough to be considered relevant."""
        return [c for c in self.search_all(question, k) if c.distance <= self.max_distance]


def format_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "NO RELEVANT HELP-DOC SECTIONS FOUND."
    parts = []
    for c in chunks:
        parts.append(f"[Source: {c.source}]\n{c.text}")
    return "\n\n".join(parts)
