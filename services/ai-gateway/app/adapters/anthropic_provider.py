"""Anthropic Messages API adapter (`POST /v1/messages`), raw httpx.

This is a REAL adapter: given a per-deployment Anthropic API key (from the
gateway's own credential store — never the `anthropic` SDK's env-var
auto-resolution), it makes genuine `/v1/messages` calls. It is unit-verified at
the httpx boundary against RECORDED real response shapes; it cannot be exercised
live in the local e2e because no Anthropic key is available there (that path is
covered by the honest "provider configured but no key -> auth error" check).

Wire differences from the OpenAI shape that this adapter handles (BRD 12 / the
Anthropic Messages API):

* `system` is a TOP-LEVEL param, not a `role: "system"` message — system-role
  messages from `ProviderRequest.messages` are extracted and concatenated into
  the top-level `system` string.
* `max_tokens` is REQUIRED.
* Sampling params (`temperature`/`top_p`/`top_k`) are OMITTED — current Anthropic
  models (Opus 4.8/4.7, Sonnet 5, Haiku 4.5, Fable 5) return HTTP 400 on them.
* Response `content` is a LIST of blocks; text is the concatenation of
  `block.text` for blocks where `block.type == "text"`.
* Token usage is `response.usage.input_tokens` / `.output_tokens`.
* `stop_reason` maps to `ProviderResult.finish_reason`.
* HTTP errors (401/403 auth, 429 rate, 5xx server) map to the gateway's
  `ProviderError`/`ProviderTimeout` taxonomy with the existing `.retryable`
  semantics (429 or >=500 retryable)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.domain.entities import ProviderDeployment
from app.domain.ports import (
    ProviderError,
    ProviderNotConfigured,
    ProviderRequest,
    ProviderResult,
    ProviderTimeout,
)
from app.utils import estimate_tokens

ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    """Real Anthropic Messages API provider (raw httpx, per-deployment key)."""

    def __init__(self, api_key: str | None, *,
                 base_url: str = ANTHROPIC_DEFAULT_BASE_URL,
                 timeout_s: float = 120.0):
        self._base_url = (base_url or ANTHROPIC_DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if not self._api_key:
            # Honest failure: a deployment named `anthropic` but no per-deployment
            # credential resolved — surfaced to the admin, never a fake success.
            raise ProviderNotConfigured(
                "anthropic deployment has no resolvable API key in the gateway "
                "credential store (endpoint_vault_ref did not resolve to a secret)"
            )
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout_s, connect=5.0),
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ payloads

    @staticmethod
    def _split_messages(messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Extract system-role messages into a single top-level `system` string
        and return (system, non_system_messages). Non-system messages keep their
        OpenAI-style {role, content} shape, which the Messages API accepts for
        user/assistant roles with string content."""
        system_parts: list[str] = []
        convo: list[dict] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
                continue
            convo.append({"role": role, "content": content})
        system = "\n\n".join(p for p in system_parts if p) or None
        return system, convo

    def _body(self, deployment: ProviderDeployment, request: ProviderRequest,
              *, stream: bool) -> dict:
        system, convo = self._split_messages(request.messages)
        body: dict = {
            "model": deployment.deployment_name,
            # max_tokens is REQUIRED by the Messages API; fall back to a bounded
            # default if the caller did not set one.
            "max_tokens": int(request.max_tokens or 1024),
            "messages": convo,
        }
        if system:
            body["system"] = system
        if stream:
            body["stream"] = True
        # NOTE: temperature/top_p/top_k are intentionally NOT forwarded — current
        # Anthropic models return 400 on them.
        return body

    def _map_status_error(self, exc: httpx.HTTPStatusError) -> ProviderError:
        # 401/403 auth (non-retryable), 429 rate (retryable), 5xx server
        # (retryable) — all via the ProviderError.retryable property.
        return ProviderError(exc.response.status_code,
                             f"anthropic http {exc.response.status_code}: "
                             f"{exc.response.text[:200]}")

    @staticmethod
    def _text_from_blocks(blocks: list[dict]) -> str:
        return "".join(b.get("text") or "" for b in blocks
                       if isinstance(b, dict) and b.get("type") == "text")

    # ------------------------------------------------------------------ interface

    async def complete(self, deployment: ProviderDeployment,
                       request: ProviderRequest) -> ProviderResult:
        client = self._http()  # raises ProviderNotConfigured when no key
        try:
            resp = await client.post("/v1/messages",
                                     json=self._body(deployment, request, stream=False))
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"anthropic timeout: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise self._map_status_error(exc) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(503, f"anthropic unreachable: {exc}") from exc

        data = resp.json()
        content = self._text_from_blocks(data.get("content") or [])
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("input_tokens")
                           or sum(estimate_tokens(m["content"]) for m in request.messages
                                  if isinstance(m.get("content"), str)))
        output_tokens = int(usage.get("output_tokens") or estimate_tokens(content))
        return ProviderResult(
            content=content, input_tokens=input_tokens, output_tokens=output_tokens,
            model=data.get("model", deployment.deployment_name),
            finish_reason=data.get("stop_reason") or "stop",
        )

    async def stream(self, deployment: ProviderDeployment,
                     request: ProviderRequest) -> AsyncIterator[dict]:
        client = self._http()
        body = self._body(deployment, request, stream=True)
        content_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0
        try:
            async with client.stream("POST", "/v1/messages", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[len("data:"):].strip()
                    if not raw:
                        continue
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    etype = evt.get("type")
                    if etype == "message_start":
                        usage = (evt.get("message") or {}).get("usage") or {}
                        input_tokens = int(usage.get("input_tokens") or input_tokens)
                    elif etype == "content_block_delta":
                        delta = evt.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            content_parts.append(delta["text"])
                            yield {"delta": delta["text"]}
                    elif etype == "message_delta":
                        usage = evt.get("usage") or {}
                        if usage.get("output_tokens") is not None:
                            output_tokens = int(usage["output_tokens"])
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"anthropic stream timeout: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise self._map_status_error(exc) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(503, f"anthropic stream unreachable: {exc}") from exc

        content = "".join(content_parts)
        if not input_tokens:
            input_tokens = sum(estimate_tokens(m["content"]) for m in request.messages
                               if isinstance(m.get("content"), str))
        if not output_tokens:
            output_tokens = estimate_tokens(content)
        yield {"usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}

    async def embed(self, deployment: ProviderDeployment, model: str,
                    inputs: list[str]) -> tuple[list[list[float]], int]:
        # The Anthropic Messages API has no embeddings endpoint — surface an
        # honest, non-retryable error rather than fabricating vectors (Rule 2).
        raise ProviderNotConfigured(
            "anthropic provider does not expose an embeddings API; configure an "
            "embedding-capable provider (e.g. ollama/openai) for the embed class"
        )
