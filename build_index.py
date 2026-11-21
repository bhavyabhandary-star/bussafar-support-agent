"""Rebuild the ChromaDB index from data/knowledge_base/bussafar_help.txt.

Usage:
  python build_index.py                     rebuild the index
  python build_index.py --query "question"  show the top 3 chunks and their distances
"""
from __future__ import annotations

import sys

import config
from rag.document_loader import load_help_docs
from rag.embeddings import EmbeddingError, GeminiEmbedder
from rag.vector_store import ensure_index


def probe(question: str) -> int:
    from rag.retrieval import Retriever

    embedder = GeminiEmbedder()
    retriever = Retriever(ensure_index(embedder), embedder)
    print(f"Top {config.TOP_K} for: {question!r}  (relevance cut-off: distance <= {config.MAX_DISTANCE})")
    for c in retriever.search_all(question):
        mark = "PASS" if c.distance <= config.MAX_DISTANCE else "drop"
        print(f"  [{mark}] {c.distance:.3f}  {c.source}")
    return 0


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--query":
        try:
            return probe(" ".join(sys.argv[2:]))
        except EmbeddingError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    try:
        chunks = load_help_docs(config.HELP_DOCS_FILE)
        print(f"Loaded {len(chunks)} chunks from {config.HELP_DOCS_FILE.name}:")
        for c in chunks:
            print(f"  {c.chunk_id:<10} {c.source}")
        collection = ensure_index(GeminiEmbedder(), force=True)
    except (EmbeddingError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Index built in {config.INDEX_DIR} with {collection.count()} chunks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
