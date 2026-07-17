"""LLM-judge client — REAL ai-gateway client (EVL-FR-012).

ALL judge model calls go THROUGH ai-gateway (budget/guardrails/metering, judge
request class, temperature 0), never direct to Ollama. The gateway is
OpenAI-compatible at ``POST /v1/chat/completions`` and needs a dual credential:
``Authorization: Bearer <virtual key>`` + ``X-Windrose-JWT: <platform jwt>``.
``model`` is a ladder alias ("windrose-auto") the gateway routes to the seeded
``fast-small -> qwen2.5:0.5b`` deployment.

eval-service mints its own short-lived platform JWT per judge call (signed with
the platform signing key; verified by ai-gateway's JWKS/PEM). The virtual key is
provisioned for eval-service's tenant."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import jwt as pyjwt


@dataclass(slots=True)
class JudgeResult:
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float = 0.0
    latency_ms: int | None = None
    trace_ref: str | None = None


class AiGatewayJudgeClient:
    def __init__(
        self,
        base_url: str,
        *,
        chat_path: str = "/v1/chat/completions",
        model: str = "windrose-auto",
        virtual_key: str | None,
        request_class: str = "judge",
        jwt_signing_key_pem: str | None = None,
        jwt_signing_kid: str | None = None,
        jwt_issuer: str = "https://identity.windrose.local",
        jwt_audience: str = "windrose",
        timeout_s: float = 120.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._path = chat_path
        self._model = model
        self._vkey = virtual_key
        self._request_class = request_class
        self._key_pem = jwt_signing_key_pem
        self._kid = jwt_signing_kid
        self._iss = jwt_issuer
        self._aud = jwt_audience
        self._timeout = timeout_s
        # Reuse one client (and its TCP+TLS connection pool) across calls rather
        # than paying a fresh handshake per judge request.
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def _mint_jwt(self, tenant_id: str) -> str:
        now = int(time.time())
        claims = {
            "sub": "svc:eval-service",
            "typ": "service",
            "tenant_id": tenant_id,
            "scopes": ["ai.invoke"],
            "iss": self._iss,
            "aud": self._aud,
            "iat": now,
            "exp": now + 300,
        }
        headers = {"kid": self._kid} if self._kid else None
        return pyjwt.encode(claims, self._key_pem, algorithm="RS256", headers=headers)

    async def judge(
        self,
        *,
        messages: list[dict],
        tenant_id: str,
        max_tokens: int = 256,
    ) -> JudgeResult:
        body = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,  # judge is always temperature 0 (gateway forces it too)
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {"x-windrose-request-class": self._request_class}
        if self._key_pem:
            headers["X-Windrose-JWT"] = self._mint_jwt(tenant_id)
        if self._vkey:
            headers["Authorization"] = f"Bearer {self._vkey}"
        url = self._base + self._path
        t0 = time.monotonic()
        client = self._http()
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        latency_ms = int((time.monotonic() - t0) * 1000)
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content", "")
        usage = data.get("usage") or {}
        return JudgeResult(
            content=content,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            model=str(data.get("model", self._model)),
            cost_usd=float(resp.headers.get("x-windrose-cost-usd", 0.0) or 0.0),
            latency_ms=latency_ms,
            trace_ref=resp.headers.get("x-windrose-request-id"),
        )
