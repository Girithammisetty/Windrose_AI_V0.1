"""Provider adapters — the provider-neutral execution layer (BRD 12 §7).

Every adapter here speaks raw `httpx` (NOT a vendor SDK) against the provider's
own HTTP API using the PER-DEPLOYMENT endpoint + credential resolved from the
gateway's own store (`app.adapters.registry`). Raw httpx keeps all adapters
structurally parallel — the whole point of a provider-neutral gateway — and,
critically, avoids vendor-SDK env-var credential auto-resolution (e.g. the
`anthropic` SDK reading `ANTHROPIC_API_KEY`), which is a security foot-gun in a
multi-tenant gateway that holds its own per-deployment secrets.

`OpenAICompatibleProvider` is the real runtime provider for every provider type
that speaks the OpenAI `/v1/chat/completions` + `/v1/embeddings` wire shape:
`ollama`, generic `openai`, and `azure_openai` (which differs only in auth
header + `api-version` query). Ollama is a configured instance of it
(`OllamaProvider`). `AnthropicProvider` (`app.adapters.anthropic_provider`)
covers the Anthropic `/v1/messages` shape. `bedrock`/`vertex` are accepted by
the config layer but their execution path raises a genuine
`ProviderNotConfigured` (see the registry) rather than a fake success — cloud
SigV4/ADC wiring is not present locally (Rule 2: honest, never faked).

`InProcessProvider` is a deterministic in-process test double: scriptable
per-deployment behaviors (errors, timeouts, streams, fixed outputs) and token
accounting. It is reachable ONLY from the unit tier (`tests/unit/`) and dev;
`main.py` wires the `ProviderRegistry` in real mode."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import AsyncIterator

import httpx

from app.domain.entities import ProviderDeployment
from app.domain.ports import (
    ProviderError,
    ProviderRequest,
    ProviderResult,
    ProviderTimeout,
)
from app.utils import estimate_tokens


class OpenAICompatibleProvider:
    """Real LLM provider over any OpenAI-compatible API (`/v1/chat/completions`,
    `/v1/embeddings`), including real SSE token streaming. Covers `ollama`,
    generic `openai`, and `azure_openai`.

    `base_url` + `api_key` come from the per-deployment credential (the gateway's
    own store), NOT from process env. `deployment.deployment_name` is the concrete
    provider-side model id sent as the request `model`.

    Auth wire differences are parameterized: OpenAI/Ollama use
    `Authorization: Bearer <key>`; Azure OpenAI uses an `api-key: <key>` header
    plus an `api-version` query param and a deployment-scoped `base_url`
    (`{endpoint}/openai/deployments/{deployment}`), so the same code path serves
    all three.

    HTTP/transport failures are mapped onto the gateway's `ProviderError`
    /`ProviderTimeout` taxonomy so the routing/failover layer (retry, circuit
    breaker, ≤3 attempts / ≤2 providers) behaves identically to production."""

    def __init__(self, base_url: str = "http://localhost:11434/v1",
                 *, timeout_s: float = 120.0, api_key: str = "ollama",
                 label: str = "openai-compatible", auth_style: str = "bearer",
                 api_version: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._api_key = api_key
        self._label = label
        self._auth_style = auth_style  # "bearer" | "api-key"
        self._api_version = api_version
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            # Only send an auth header when a credential is actually present — a
            # local OpenAI-compatible endpoint (e.g. Ollama) needs none, and an
            # empty "Bearer " / "api-key: " is an illegal header value.
            headers: dict = {}
            if self._api_key:
                if self._auth_style == "api-key":  # azure_openai
                    headers["api-key"] = self._api_key
                else:
                    headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout_s, connect=5.0),
                headers=headers,
            )
        return self._client

    @property
    def _params(self) -> dict | None:
        return {"api-version": self._api_version} if self._api_version else None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ payloads

    @staticmethod
    def _chat_payload(deployment: ProviderDeployment, request: ProviderRequest,
                      *, stream: bool) -> dict:
        payload: dict = {
            "model": deployment.deployment_name,
            "messages": request.messages,
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.response_format:
            payload["response_format"] = request.response_format
        if request.tools:
            payload["tools"] = request.tools
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _map_status_error(self, exc: httpx.HTTPStatusError) -> ProviderError:
        return ProviderError(exc.response.status_code,
                             f"{self._label} http {exc.response.status_code}: "
                             f"{exc.response.text[:200]}")

    # ------------------------------------------------------------------ interface

    async def complete(self, deployment: ProviderDeployment,
                       request: ProviderRequest) -> ProviderResult:
        try:
            resp = await self._http().post(
                "/chat/completions", params=self._params,
                json=self._chat_payload(deployment, request, stream=False),
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"{self._label} timeout: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise self._map_status_error(exc) from exc
        except httpx.HTTPError as exc:  # connect/read/transport errors
            raise ProviderError(503, f"{self._label} unreachable: {exc}") from exc

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens")
                           or sum(estimate_tokens(m["content"]) for m in request.messages
                                  if isinstance(m.get("content"), str)))
        output_tokens = int(usage.get("completion_tokens") or estimate_tokens(content))
        return ProviderResult(
            content=content, input_tokens=input_tokens, output_tokens=output_tokens,
            model=data.get("model", deployment.deployment_name),
            finish_reason=choice.get("finish_reason") or "stop",
        )

    async def stream(self, deployment: ProviderDeployment,
                     request: ProviderRequest) -> AsyncIterator[dict]:
        payload = self._chat_payload(deployment, request, stream=True)
        content_parts: list[str] = []
        usage: dict | None = None
        try:
            async with self._http().stream(
                "POST", "/chat/completions", params=self._params, json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for ch in chunk.get("choices") or []:
                        delta = (ch.get("delta") or {}).get("content")
                        if delta:
                            content_parts.append(delta)
                            yield {"delta": delta}
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"{self._label} stream timeout: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise self._map_status_error(exc) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(503, f"{self._label} stream unreachable: {exc}") from exc

        content = "".join(content_parts)
        input_tokens = int((usage or {}).get("prompt_tokens")
                           or sum(estimate_tokens(m["content"]) for m in request.messages
                                  if isinstance(m.get("content"), str)))
        output_tokens = int((usage or {}).get("completion_tokens")
                            or estimate_tokens(content))
        # usage chunk always emitted (stream_options.include_usage, AIG-FR-010)
        yield {"usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}

    async def embed(self, deployment: ProviderDeployment, model: str,
                    inputs: list[str]) -> tuple[list[list[float]], int]:
        try:
            resp = await self._http().post(
                "/embeddings", params=self._params,
                json={"model": deployment.deployment_name, "input": inputs},
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"{self._label} embed timeout: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise self._map_status_error(exc) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(503, f"{self._label} embed unreachable: {exc}") from exc

        data = resp.json()
        rows = sorted(data.get("data") or [], key=lambda r: r.get("index", 0))
        vectors = [list(r.get("embedding") or []) for r in rows]
        usage = data.get("usage") or {}
        tokens = int(usage.get("prompt_tokens")
                     or sum(estimate_tokens(t) for t in inputs))
        return vectors, tokens


class OllamaProvider(OpenAICompatibleProvider):
    """`OpenAICompatibleProvider` pinned to a local Ollama server (the real
    local LLM used in e2e). Kept as a distinct class so existing wiring/tests
    that import `OllamaProvider` are unchanged; `provider="ollama"` deployments
    resolve to this via the registry."""

    def __init__(self, base_url: str = "http://localhost:11434/v1",
                 *, timeout_s: float = 120.0, api_key: str = "ollama"):
        super().__init__(base_url, timeout_s=timeout_s, api_key=api_key,
                         label="ollama", auth_style="bearer")


class InProcessProvider:
    """Scriptable in-process test double used by the unit tier and dev only."""

    def __init__(self):
        self._scripts: dict[str, deque] = {}
        self.calls: list[tuple[str, ProviderRequest]] = []
        self.billed_tokens: dict[str, int] = {}  # deployment_name -> tokens

    # ------------------------------------------------------------------ scripting

    def script(self, deployment_name: str, *outcomes: dict) -> None:
        """Outcomes are consumed in order; when exhausted, echo behavior applies.
        Shapes: {"error": 500} | {"timeout": True} | {"content": "..."} |
        {"stream": ["chunk", ...]} | {"stream_error": 500}."""
        self._scripts.setdefault(deployment_name, deque()).extend(outcomes)

    def _next(self, deployment_name: str) -> dict | None:
        q = self._scripts.get(deployment_name)
        return q.popleft() if q else None

    def _bill(self, deployment_name: str, tokens: int) -> None:
        self.billed_tokens[deployment_name] = (
            self.billed_tokens.get(deployment_name, 0) + tokens
        )

    # ------------------------------------------------------------------ interface

    async def complete(self, deployment: ProviderDeployment,
                       request: ProviderRequest) -> ProviderResult:
        self.calls.append((deployment.deployment_name, request))
        outcome = self._next(deployment.deployment_name)
        if outcome:
            if outcome.get("timeout"):
                raise ProviderTimeout()
            if "error" in outcome:
                raise ProviderError(outcome["error"])
            if "content" in outcome:
                content = outcome["content"]
                input_tokens = sum(
                    estimate_tokens(m["content"]) for m in request.messages
                    if isinstance(m.get("content"), str)
                )
                output_tokens = outcome.get("output_tokens", estimate_tokens(content))
                self._bill(deployment.deployment_name, input_tokens + output_tokens)
                return ProviderResult(content=content, input_tokens=input_tokens,
                                      output_tokens=output_tokens,
                                      model=deployment.deployment_name)
        # default: deterministic echo
        last_user = next(
            (m["content"] for m in reversed(request.messages)
             if m.get("role") == "user" and isinstance(m.get("content"), str)),
            "",
        )
        content = f"echo({deployment.deployment_name}): {last_user}"
        input_tokens = sum(
            estimate_tokens(m["content"]) for m in request.messages
            if isinstance(m.get("content"), str)
        )
        output_tokens = estimate_tokens(content)
        self._bill(deployment.deployment_name, input_tokens + output_tokens)
        return ProviderResult(content=content, input_tokens=input_tokens,
                              output_tokens=output_tokens,
                              model=deployment.deployment_name)

    async def stream(self, deployment: ProviderDeployment,
                     request: ProviderRequest) -> AsyncIterator[dict]:
        self.calls.append((deployment.deployment_name, request))
        outcome = self._next(deployment.deployment_name)
        if outcome:
            if outcome.get("timeout"):
                raise ProviderTimeout()
            if "error" in outcome or "stream_error" in outcome:
                raise ProviderError(outcome.get("error") or outcome["stream_error"])
            chunks = outcome.get("stream")
        else:
            chunks = None
        if chunks is None:
            last_user = next(
                (m["content"] for m in reversed(request.messages)
                 if m.get("role") == "user" and isinstance(m.get("content"), str)),
                "",
            )
            text = f"echo({deployment.deployment_name}): {last_user}"
            step = max(1, len(text) // 3)
            chunks = [text[i:i + step] for i in range(0, len(text), step)]
        input_tokens = sum(
            estimate_tokens(m["content"]) for m in request.messages
            if isinstance(m.get("content"), str)
        )
        output_tokens = sum(estimate_tokens(c) for c in chunks)
        for c in chunks:
            yield {"delta": c}
        self._bill(deployment.deployment_name, input_tokens + output_tokens)
        # usage chunk always emitted (stream_options.include_usage, AIG-FR-010)
        yield {"usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}

    async def embed(self, deployment: ProviderDeployment, model: str,
                    inputs: list[str]) -> tuple[list[list[float]], int]:
        outcome = self._next(deployment.deployment_name)
        if outcome:
            if outcome.get("timeout"):
                raise ProviderTimeout()
            if "error" in outcome:
                raise ProviderError(outcome["error"])
        from app.adapters.embeddings import hash_embedding

        vectors = [hash_embedding(t, dim=16) for t in inputs]
        tokens = sum(estimate_tokens(t) for t in inputs)
        self._bill(deployment.deployment_name, tokens)
        return vectors, tokens
