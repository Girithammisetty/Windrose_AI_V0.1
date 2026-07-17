"""Embeddings for verified-query semantic search (SEM-FR-041).

Runtime: ``OpenAIEmbeddingClient`` — a real HTTP client for an OpenAI-compatible
``/v1/embeddings`` endpoint. The platform embeds via ai-gateway (which serves a
real model with budget/pinning); for a self-contained local runtime it points at
Ollama's OpenAI-compatible endpoint serving the real ``nomic-embed-text`` model
(768-dim). Either way the vector is a real learned embedding, not a hash.

Unit tests: ``LocalHashEmbedding`` — a deterministic, network-free double kept
ONLY for the unit tier (never reachable from ``app.main`` when
``use_real_adapters`` is True).
"""

from __future__ import annotations

import hashlib
import math
import re

import httpx

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class LocalHashEmbedding:
    """Bag-of-words feature hashing into `dim` buckets, L2-normalized.
    Deterministic: same text -> same vector, similar texts -> high cosine.
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
    ai-gateway in the deployed platform (BRD 12: model pinning + budget headers).
    Returns the raw model embedding — a real learned vector, never a hash fake.
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
        # Reuse one client (and its TCP+TLS connection pool) across calls rather
        # than paying a fresh handshake per request.
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def embed(self, tenant_id: str, text: str) -> list[float]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        # Forward tenant for per-tenant budgeting/attribution at the gateway.
        headers["x-windrose-tenant-id"] = tenant_id
        client = self._http()
        resp = await client.post(
            f"{self.base_url}/embeddings",
            json={"model": self.model, "input": text or ""},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return [float(x) for x in data["data"][0]["embedding"]]


# Backwards-compatible alias: the port name used across the service.
AiGatewayEmbeddingClient = OpenAIEmbeddingClient
