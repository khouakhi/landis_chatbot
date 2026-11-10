"""Mistral embeddings, L2-normalised for cosine search in pgvector."""

from __future__ import annotations

from typing import Sequence

import numpy as np

from . import config as cfg
from .retry import call_with_retry


def get_client():
    if not cfg.MISTRAL_API_KEY:
        raise RuntimeError("MISTRAL_API_KEY is not set. Add it to .env in the code folder.")
    from mistralai.client import Mistral

    return Mistral(api_key=cfg.MISTRAL_API_KEY)


def _normalise(vecs: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return (vecs / norms).astype("float32")


def embed_texts(client, texts: Sequence[str], normalise: bool = True) -> np.ndarray:
    """Return a ``(n, d)`` float32 array. Empty input returns an empty array."""
    if not texts:
        return np.zeros((0, cfg.EMBEDDING_DIMENSION), dtype="float32")
    rows: list[np.ndarray] = []
    batch: list[str] = []
    for text in texts:
        batch.append(text)
        if len(batch) >= cfg.EMBED_BATCH_SIZE:
            rows.append(_one_batch(client, batch, normalise))
            batch = []
    if batch:
        rows.append(_one_batch(client, batch, normalise))
    matrix = np.vstack(rows)
    if matrix.shape[1] != cfg.EMBEDDING_DIMENSION:
        raise RuntimeError(
            f"Expected embedding dimension {cfg.EMBEDDING_DIMENSION}, got {matrix.shape[1]}."
        )
    return matrix


def _one_batch(client, batch: list[str], normalise: bool) -> np.ndarray:
    def call():
        return client.embeddings.create(model=cfg.MISTRAL_EMBED_MODEL, inputs=batch)

    response = call_with_retry(call)
    if not response or not response.data:
        raise RuntimeError("Mistral embeddings response had no data.")

    def _order(item) -> int:
        index = getattr(item, "index", None)
        return int(index) if index is not None else 0

    items = sorted(response.data, key=_order)
    vectors = []
    for item in items:
        embedding = item.embedding
        if embedding is None:
            raise RuntimeError("Mistral embedding entry had no vector.")
        vectors.append(embedding)
    matrix = np.array(vectors, dtype="float32")
    if normalise:
        matrix = _normalise(matrix)
    return matrix


def embed_query(client, text: str) -> np.ndarray:
    """Single query vector, shape ``(d,)``, L2-normalised."""
    return embed_texts(client, [text], normalise=True).reshape(-1)
