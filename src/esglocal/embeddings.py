"""Text embeddings — local model when available, deterministic offline fallback.

``OllamaEmbedder`` uses the local Ollama ``/api/embed`` endpoint.
``HashingEmbedder`` is a dependency-free bag-of-words (+ bigrams) hashing
vectoriser; it is weaker than a neural model but needs no download at all,
so the app keeps working on an air-gapped machine with no embedding model.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import numpy as np

_WORD = re.compile(r"[a-z0-9]+")
_STOP = set(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "we",
        "were",
        "will",
        "with",
        "our",
        "this",
        "these",
        "those",
        "not",
        "no",
    ]
)


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _features(self, text: str) -> list[str]:
        words = [w for w in _WORD.findall(text.lower()) if w not in _STOP]
        return words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for feat in self._features(text):
                h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "big")
                sign = 1.0 if (h >> 63) & 1 else -1.0
                out[i, h % self.dim] += sign
            norm = np.linalg.norm(out[i])
            if norm > 0:
                out[i] /= norm
        return out


class OllamaEmbedder:
    def __init__(self, client, model: str):
        self.client = client
        self.model = model
        self.name = f"ollama:{model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.asarray(self.client.embed(texts, model=self.model), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0 or math.isnan(denom):
        return 0.0
    return float(np.dot(a, b) / denom)


def get_embedder(client=None, model: str | None = None) -> Embedder:
    """Prefer the local Ollama embedding model; fall back to hashing if unavailable."""
    if client is not None and model:
        try:
            if model in client.list_models() or any(
                m.split(":")[0] == model.split(":")[0] for m in client.list_models()
            ):
                emb = OllamaEmbedder(client, model)
                emb.embed(["probe"])
                return emb
        except Exception:  # noqa: BLE001,S110 - any failure means "use the offline fallback"
            pass
    return HashingEmbedder()
