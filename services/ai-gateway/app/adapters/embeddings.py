"""Embedding adapters for the semantic *cache* similarity tier.

`hash_embedding` is a deterministic bag-of-words embedding used only to score
cache-entry similarity: it is intentionally model-free so the semantic-cache
tier is deterministic and testable, and so the cache never draws budget or
issues a network call on the read path. This is a real, runtime component (not
a stub) — distinct from the `/v1/embeddings` data-plane endpoint, which returns
genuine model vectors via the real Ollama provider (`app.adapters.providers
.OllamaProvider.embed`, e.g. `nomic-embed-text`)."""

from __future__ import annotations

import hashlib
import math
import re

_TOKEN = re.compile(r"[a-z0-9]+")


def hash_embedding(text: str, dim: int = 256) -> list[float]:
    vec = [0.0] * dim
    for token in _TOKEN.findall(text.lower()):
        h = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


class HashEmbedder:
    def __init__(self, dim: int = 256):
        self.dim = dim

    async def embed(self, text: str) -> list[float]:
        return hash_embedding(text, self.dim)
