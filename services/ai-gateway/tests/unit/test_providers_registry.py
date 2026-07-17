"""Unit tests for the provider-agnostic execution layer:

* `OpenAICompatibleProvider` against a RECORDED OpenAI /v1/chat/completions shape.
* `AnthropicProvider` request construction (x-api-key, anthropic-version,
  /v1/messages, system extracted to top-level, NO temperature) + parsing a
  RECORDED Anthropic /v1/messages response, plus honest error mapping and the
  no-key ProviderNotConfigured path.
* `ProviderRegistry` dispatch by provider type (incl. bedrock/vertex honest
  ProviderNotConfigured) and per-deployment credential resolution.
* `PriceTable.quote_for` per-(provider, model) pricing.

httpx is mocked at the transport boundary by patching the client constructor to
inject an `httpx.MockTransport`, so the adapter's OWN header/body construction is
exercised (the constructor kwargs are captured for assertion)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.anthropic_provider import AnthropicProvider
from app.adapters.providers import OpenAICompatibleProvider
from app.adapters.registry import ProviderRegistry, resolve_credential
from app.config import Settings
from app.domain.entities import ProviderDeployment
from app.domain.ports import (
    ProviderError,
    ProviderNotConfigured,
    ProviderRequest,
    ProviderTimeout,
)
from app.domain.pricing import PriceTable

# --- RECORDED real response shapes ------------------------------------------

OPENAI_CHAT_RESPONSE = {
    "id": "chatcmpl-abc123",
    "object": "chat.completion",
    "created": 1_700_000_000,
    "model": "gpt-4o-mini",
    "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": "Hello from OpenAI."},
        "finish_reason": "stop",
    }],
    "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
}

# Real Anthropic Messages API /v1/messages response shape.
ANTHROPIC_MESSAGES_RESPONSE = {
    "id": "msg_01ABC",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-4-8",
    "content": [
        {"type": "text", "text": "Hi there,"},
        {"type": "text", "text": " from Claude."},
    ],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 23, "output_tokens": 7},
}


def _deployment(provider: str, name: str = "m", ref: str = "http://x/v1"):
    return ProviderDeployment(
        id="d1", tenant_id="t", provider=provider, model_family="fast-small",
        deployment_name=name, region="r", cloud="aws", endpoint_vault_ref=ref,
        tpm_limit=0, rpm_limit=0, priority=1,
    )


def _patch_httpx(monkeypatch, handler, captured):
    """Force adapters' httpx.AsyncClient to use a MockTransport, capturing the
    constructor kwargs (so we can assert on the headers the adapter set)."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        captured.append(kwargs)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


# --- OpenAI-compatible adapter ----------------------------------------------


@pytest.mark.asyncio
async def test_openai_compatible_complete_parses_recorded_shape(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=OPENAI_CHAT_RESPONSE)

    _patch_httpx(monkeypatch, handler, [])
    adapter = OpenAICompatibleProvider("http://host/v1", api_key="sk-test",
                                       label="openai")
    dep = _deployment("openai", name="gpt-4o-mini")
    result = await adapter.complete(dep, ProviderRequest(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}],
        max_tokens=64, temperature=0.5))

    assert result.content == "Hello from OpenAI."
    assert result.input_tokens == 11 and result.output_tokens == 4
    assert result.finish_reason == "stop"
    assert seen["url"].endswith("/v1/chat/completions")
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_openai_compatible_error_mapping(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    _patch_httpx(monkeypatch, handler, [])
    adapter = OpenAICompatibleProvider("http://host/v1", api_key="k")
    with pytest.raises(ProviderError) as exc:
        await adapter.complete(_deployment("openai"), ProviderRequest(
            model="m", messages=[{"role": "user", "content": "hi"}],
            max_tokens=8, temperature=0.0))
    assert exc.value.status == 429 and exc.value.retryable is True


@pytest.mark.asyncio
async def test_azure_openai_uses_api_key_header_and_api_version(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["api_key"] = request.headers.get("api-key")
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=OPENAI_CHAT_RESPONSE)

    _patch_httpx(monkeypatch, handler, [])
    adapter = OpenAICompatibleProvider(
        "https://acct.openai.azure.com/openai/deployments/gpt", api_key="azkey",
        label="azure_openai", auth_style="api-key", api_version="2024-06-01")
    await adapter.complete(_deployment("azure_openai"), ProviderRequest(
        model="m", messages=[{"role": "user", "content": "hi"}],
        max_tokens=8, temperature=0.0))
    assert seen["api_key"] == "azkey" and seen["auth"] is None
    assert "api-version=2024-06-01" in seen["url"]


# --- Anthropic adapter ------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_builds_correct_request_and_parses_response(monkeypatch):
    seen = {}
    captured: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["x_api_key"] = request.headers.get("x-api-key")
        seen["version"] = request.headers.get("anthropic-version")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ANTHROPIC_MESSAGES_RESPONSE)

    _patch_httpx(monkeypatch, handler, captured)
    adapter = AnthropicProvider("sk-ant-123")
    dep = _deployment("anthropic", name="claude-opus-4-8")
    result = await adapter.complete(dep, ProviderRequest(
        model="claude-opus-4-8",
        messages=[
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "hi"},
        ],
        max_tokens=100, temperature=0.7))  # temperature MUST be dropped

    # request construction
    assert seen["url"].endswith("/v1/messages")
    assert seen["x_api_key"] == "sk-ant-123"
    assert seen["version"] == "2023-06-01"
    body = seen["body"]
    assert body["model"] == "claude-opus-4-8"
    assert body["max_tokens"] == 100
    assert body["system"] == "You are terse."  # extracted to top-level
    assert body["messages"] == [{"role": "user", "content": "hi"}]  # system removed
    assert "temperature" not in body and "top_p" not in body and "top_k" not in body
    # response parsing: text blocks concatenated, usage mapped, stop_reason mapped
    assert result.content == "Hi there, from Claude."
    assert result.input_tokens == 23 and result.output_tokens == 7
    assert result.finish_reason == "end_turn"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,retryable", [(401, False), (403, False),
                                              (429, True), (500, True)])
async def test_anthropic_error_mapping(monkeypatch, status, retryable):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="err")

    _patch_httpx(monkeypatch, handler, [])
    adapter = AnthropicProvider("sk-ant-123")
    with pytest.raises(ProviderError) as exc:
        await adapter.complete(_deployment("anthropic"), ProviderRequest(
            model="m", messages=[{"role": "user", "content": "hi"}],
            max_tokens=8, temperature=0.0))
    assert exc.value.status == status
    assert exc.value.retryable is retryable


@pytest.mark.asyncio
async def test_anthropic_no_key_is_honest_not_configured():
    adapter = AnthropicProvider(None)
    with pytest.raises(ProviderNotConfigured):
        await adapter.complete(_deployment("anthropic"), ProviderRequest(
            model="m", messages=[{"role": "user", "content": "hi"}],
            max_tokens=8, temperature=0.0))


@pytest.mark.asyncio
async def test_anthropic_timeout_maps_to_provider_timeout(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    _patch_httpx(monkeypatch, handler, [])
    adapter = AnthropicProvider("sk-ant-123")
    with pytest.raises(ProviderTimeout):
        await adapter.complete(_deployment("anthropic"), ProviderRequest(
            model="m", messages=[{"role": "user", "content": "hi"}],
            max_tokens=8, temperature=0.0))


@pytest.mark.asyncio
async def test_anthropic_has_no_embeddings():
    adapter = AnthropicProvider("sk-ant-123")
    with pytest.raises(ProviderNotConfigured):
        await adapter.embed(_deployment("anthropic"), "m", ["x"])


# --- Registry dispatch ------------------------------------------------------


def test_registry_dispatch_by_provider_type():
    reg = ProviderRegistry(Settings())
    ol = reg._adapter_for(_deployment("ollama"))
    assert isinstance(ol, OpenAICompatibleProvider) and ol._label == "ollama"
    oa = reg._adapter_for(_deployment("openai", ref="http://h/v1"))
    assert isinstance(oa, OpenAICompatibleProvider) and oa._auth_style == "bearer"
    az = reg._adapter_for(_deployment("azure_openai", ref="https://a/openai/deployments/x"))
    assert isinstance(az, OpenAICompatibleProvider) and az._auth_style == "api-key"
    an = reg._adapter_for(_deployment("anthropic", ref="secret/none"))
    assert isinstance(an, AnthropicProvider)


@pytest.mark.parametrize("provider", ["bedrock", "vertex"])
def test_registry_bedrock_vertex_are_honest_not_configured(provider):
    reg = ProviderRegistry(Settings())
    with pytest.raises(ProviderNotConfigured):
        reg._adapter_for(_deployment(provider))


def test_registry_caches_adapter_instances():
    reg = ProviderRegistry(Settings())
    d = _deployment("openai", ref="http://h/v1")
    assert reg._adapter_for(d) is reg._adapter_for(d)


# --- Credential resolution --------------------------------------------------


def test_resolve_credential_url_ref_and_provider_key(monkeypatch):
    monkeypatch.setenv("AIG_PROVIDER_KEY__OPENAI", "sk-live")
    cred = resolve_credential(_deployment("openai", ref="http://h:1/v1"), Settings())
    assert cred.base_url == "http://h:1/v1" and cred.api_key == "sk-live"


def test_resolve_credential_secret_json(monkeypatch):
    monkeypatch.setenv(
        "AIG_SECRET__SECRET_AI_ANTHROPIC_PROD",
        json.dumps({"base_url": "https://api.anthropic.com", "api_key": "sk-ant-x"}),
    )
    cred = resolve_credential(
        _deployment("anthropic", ref="secret/ai/anthropic/prod"), Settings())
    assert cred.api_key == "sk-ant-x"


def test_resolve_credential_ollama_defaults():
    cred = resolve_credential(_deployment("ollama", ref="secret/ai/ollama/x"),
                              Settings(ollama_base_url="http://ol/v1"))
    assert cred.base_url == "http://ol/v1" and cred.api_key == "ollama"


# --- Per-(provider, model) pricing ------------------------------------------


def test_price_table_exact_provider_model():
    q = PriceTable(version="v1").quote_for("anthropic", "claude-opus-4-8", "frontier")
    assert q.input_per_1k == 0.005 and q.output_per_1k == 0.025
    assert q.source == "provider_model" and q.provider == "anthropic"


def test_price_table_ollama_is_free():
    q = PriceTable(version="v1").quote_for("ollama", "qwen2.5:0.5b", "fast-small")
    assert q.input_per_1k == 0.0 and q.output_per_1k == 0.0
    assert q.source == "provider_zero"


def test_price_table_falls_back_to_alias():
    # openai model with no published per-model price -> alias-tier price.
    q = PriceTable(version="v1").quote_for("openai", "gpt-4o-mini", "balanced")
    assert q.input_per_1k == 0.003 and q.output_per_1k == 0.015
    assert q.source == "alias" and q.provider == "openai"
