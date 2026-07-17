"""LLM adapter — REAL ai-gateway client (ART-FR-012).

ALL model calls go THROUGH ai-gateway (budget/guardrails/metering), never direct
to Ollama. The gateway is OpenAI-compatible at ``POST /v1/chat/completions`` and
needs a dual credential: ``Authorization: Bearer <virtual key>`` +
``X-Windrose-JWT: <jwt>``. ``model`` is a ladder alias ("windrose-auto"), which
the gateway routes to the seeded ``fast-small -> qwen2.5:0.5b`` deployment.

The virtual key is tenant-scoped (ai-gateway rejects a key/tenant mismatch as
"invalid or revoked", indistinguishable from a truly bad key). agent-runtime is
ONE shared process for every tenant, so a single fixed key only ever works for
one tenant and 401s any other tenant's call that happens to interleave on this
process — prefer ``vkey_provider`` (mints/caches a real per-tenant key via
ai-gateway's admin API, see app.adapters.vkeys.TenantVirtualKeyProvider) over
the static ``virtual_key`` fallback, which exists only for a single-tenant dev
override via ``AR_AI_GATEWAY_VIRTUAL_KEY``.
"""

from __future__ import annotations

import httpx

from app.domain.ports import LlmResult


class AiGatewayLlmClient:
    def __init__(
        self,
        base_url: str,
        *,
        chat_path: str = "/v1/chat/completions",
        model: str = "windrose-auto",
        virtual_key: str | None = None,
        vkey_provider=None,  # async callable(tenant_id) -> vkey str; takes precedence
        jwt_provider,  # callable(tenant_id) -> jwt str
        request_class: str = "chat",
        temperature: float = 0.2,
        max_tokens: int = 512,
        timeout_s: float = 120.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._path = chat_path
        self._model = model
        self._vkey = virtual_key
        self._vkey_provider = vkey_provider
        self._jwt_provider = jwt_provider
        self._request_class = request_class
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout_s

    async def chat(
        self,
        *,
        messages: list[dict],
        tenant_id: str,
        response_format: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LlmResult:
        body: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature if temperature is None else temperature,
            "max_tokens": self._max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if response_format is not None:
            body["response_format"] = response_format
        headers = {
            "X-Windrose-JWT": self._jwt_provider(tenant_id),
            "x-windrose-request-class": self._request_class,
        }
        if self._vkey_provider is not None:
            headers["Authorization"] = f"Bearer {await self._vkey_provider(tenant_id)}"
        elif self._vkey:
            headers["Authorization"] = f"Bearer {self._vkey}"
        url = self._base + self._path
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content", "")
        usage = data.get("usage") or {}
        return LlmResult(
            content=content,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            model=str(data.get("model", self._model)),
            deployment=resp.headers.get("x-windrose-deployment"),
        )
