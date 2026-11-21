"""Gemini embedding wrapper with batching and retry on rate limits."""
from __future__ import annotations

import time
from typing import Protocol, Sequence

import config


class Embedder(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbeddingError(RuntimeError):
    pass


class GeminiEmbedder:
    """Embeds text with the Gemini embedding model (google-genai SDK)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        batch_size: int = 20,
        max_retries: int = 5,
    ) -> None:
        api_key = api_key or config.google_api_key()
        if not api_key:
            raise EmbeddingError(
                "GOOGLE_API_KEY is not set. Add it to .env or Streamlit secrets."
            )
        from google import genai  # imported lazily so tests run without the SDK configured

        self._client = genai.Client(api_key=api_key)
        self.model = model or config.EMBED_MODEL
        self.batch_size = batch_size
        self.max_retries = max_retries

    def _embed(self, texts: Sequence[str], task_type: str) -> list[list[float]]:
        from google.genai import errors, types

        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            delay = 5.0
            for attempt in range(self.max_retries):
                try:
                    resp = self._client.models.embed_content(
                        model=self.model,
                        contents=batch,
                        config=types.EmbedContentConfig(task_type=task_type),
                    )
                    out.extend(list(e.values) for e in resp.embeddings)
                    break
                except errors.APIError as exc:  # 429 = quota busy, 5xx = transient
                    code = getattr(exc, "code", None)
                    if code in (429, 500, 503) and attempt < self.max_retries - 1:
                        time.sleep(delay)
                        delay *= 2
                        continue
                    raise EmbeddingError(f"Gemini embedding failed ({code}): {exc}") from exc
        return out

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]
