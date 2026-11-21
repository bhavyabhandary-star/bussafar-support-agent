"""ChromaDB persistence for the help-doc chunks."""
from __future__ import annotations

from pathlib import Path

import chromadb

import config
from rag.document_loader import Chunk, file_fingerprint, load_help_docs
from rag.embeddings import Embedder


def _client(index_dir: Path) -> chromadb.ClientAPI:
    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(index_dir), settings=chromadb.Settings(anonymized_telemetry=False)
    )


def build_index(
    chunks: list[Chunk],
    embedder: Embedder,
    index_dir: Path = config.INDEX_DIR,
    fingerprint: str = "",
    collection_name: str = config.COLLECTION_NAME,
):
    """(Re)create the collection from scratch and add every chunk."""
    client = _client(index_dir)
    try:
        client.delete_collection(collection_name)
    except Exception:  # noqa: BLE001 - collection did not exist yet
        pass
    collection = client.create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine", "fingerprint": fingerprint or "none"},
    )
    vectors = embedder.embed_documents([c.text for c in chunks])
    collection.add(
        ids=[c.chunk_id for c in chunks],
        documents=[c.text for c in chunks],
        embeddings=vectors,
        metadatas=[
            {
                "kind": c.kind,
                "title": c.title,
                "source": c.source,
                "order": c.order,
                "source_file": c.metadata.get("source_file", ""),
            }
            for c in chunks
        ],
    )
    return collection


def get_collection(index_dir: Path = config.INDEX_DIR, collection_name: str = config.COLLECTION_NAME):
    try:
        return _client(index_dir).get_collection(collection_name)
    except Exception:  # noqa: BLE001 - not built yet
        return None


def ensure_index(
    embedder: Embedder,
    help_file: Path = config.HELP_DOCS_FILE,
    index_dir: Path = config.INDEX_DIR,
    collection_name: str = config.COLLECTION_NAME,
    force: bool = False,
):
    """Return the collection, building it if it is missing, empty or stale."""
    fingerprint = file_fingerprint(help_file)
    collection = None if force else get_collection(index_dir, collection_name)
    if collection is not None:
        meta = collection.metadata or {}
        if collection.count() > 0 and meta.get("fingerprint") == fingerprint:
            return collection
    chunks = load_help_docs(help_file)
    return build_index(chunks, embedder, index_dir, fingerprint, collection_name)
