"""Embeddings (MEM-FR-002).

Runtime: ``OpenAIEmbeddingClient`` — a real HTTP client for an OpenAI-compatible
``/v1/embeddings`` endpoint. The platform embeds via ai-gateway; for a
self-contained local runtime it points at Ollama's endpoint serving the real
``nomic-embed-text`` model (768-dim). The vector is a real learned embedding.

Unit tests: ``LocalHashEmbedding`` — a deterministic, network-free double kept
ONLY for the unit tier (never reachable from ``app.main`` when
``use_real_adapters`` is True).
"""

from __future__ import annotations

import hashlib
import math
import re

import httpx

from app.domain.errors import EmbeddingUnavailable

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Re-exported for callers that imported it from here historically.
__all__ = ["EmbeddingUnavailable", "LocalHashEmbedding",
           "OpenAIEmbeddingClient", "AiGatewayEmbeddingClient"]


class LocalHashEmbedding:
    """Bag-of-words feature hashing into `dim` buckets, L2-normalized.
    Deterministic: same text -> same vector; similar texts -> high cosine.
    Unit-test double only — NOT wired into the real runtime container."""

    def __init__(self, dim: int = 768):
        self.dim = dim

    async def embed(self, tenant_id: str, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN_RE.findall((text or "").lower()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm else vec


class OpenAIEmbeddingClient:
    """Real embeddings over an OpenAI-compatible ``/v1/embeddings`` server.

    Default base URL is Ollama's endpoint (``nomic-embed-text``, 768-dim) so the
    local runtime is fully real and self-contained; point ``base_url`` at
    ai-gateway in the deployed platform. Raises ``EmbeddingUnavailable`` on
    transport failure so the write path can queue and retrieval can degrade
    (BR-2). Returns the raw model embedding — a real learned vector, never a hash.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        *,
        model: str = "nomic-embed-text",
        api_key: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s

    async def embed(self, tenant_id: str, text: str) -> list[float]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        headers["x-windrose-tenant-id"] = tenant_id
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(
                    f"{self.base_url}/embeddings",
                    json={"model": self.model, "input": text or ""},
                    headers=headers,
                )
                resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailable(f"embeddings endpoint unavailable: {exc}") from exc
        return [float(x) for x in data["data"][0]["embedding"]]


AiGatewayEmbeddingClient = OpenAIEmbeddingClient
