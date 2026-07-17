"""Provider registry / factory — the core of the provider-agnostic gateway.

The pipeline (`GatewayService`) holds ONE `provider: ProviderClient`, but every
call already carries the `ProviderDeployment`. `ProviderRegistry` implements the
`ProviderClient` protocol and, per call, resolves `deployment.provider` -> the
right real adapter (constructed with that deployment's own endpoint + credential
from the gateway's store) and dispatches to it. So a single `provider_client`
becomes provider-agnostic dispatch WITHOUT touching the pipeline, and the
circuit-breaker / failover semantics (keyed on `deployment.id`) are preserved.

Provider -> adapter:
  * ollama        -> OpenAICompatibleProvider (bearer, ollama defaults)
  * openai        -> OpenAICompatibleProvider (bearer)
  * azure_openai  -> OpenAICompatibleProvider (api-key header + api-version query)
  * anthropic     -> AnthropicProvider (/v1/messages, x-api-key)
  * bedrock       -> ProviderNotConfigured (real SigV4 wiring absent locally)
  * vertex        -> ProviderNotConfigured (real ADC wiring absent locally)

bedrock/vertex are ACCEPTED by the admin/config layer (a deployment can be
created) but their EXECUTION path raises a genuine, typed, non-retryable
`ProviderNotConfigured` surfaced to the admin — never a silent fake success
(Rule 2).

Credential resolution (`resolve_credential`) reads the gateway's OWN store, keyed
on the deployment's `endpoint_vault_ref`, NOT any vendor-SDK env auto-resolution:
  * ref is an http(s) URL  -> that URL is the base_url (a self-hosted endpoint
    URL is not itself a secret); the api_key comes from a provider-level env var
    if present (`AIG_PROVIDER_KEY__<PROVIDER>`). This is the local/dev form and
    is what the live OpenAI-compatible-against-Ollama round trip uses.
  * ref is a logical secret name -> `AIG_SECRET__<SLUG>` env var (a 12-factor
    mounted secret) holding either JSON `{base_url, api_key, api_version}`, a
    `base_url|api_key` pipe pair, or a bare api_key. This is the production form
    (the ref points at a Vault/secret path materialised as a mounted env var).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator

from app.adapters.anthropic_provider import ANTHROPIC_DEFAULT_BASE_URL, AnthropicProvider
from app.adapters.providers import OpenAICompatibleProvider
from app.config import Settings
from app.domain.entities import ProviderDeployment
from app.domain.ports import (
    ProviderCredential,
    ProviderNotConfigured,
    ProviderRequest,
    ProviderResult,
)

# Provider types whose execution path is honestly NOT runnable in this
# deployment (accepted at config time, real cloud-cred wiring absent).
UNRUNNABLE_PROVIDERS = ("bedrock", "vertex")

# Provider types this gateway can actually execute end-to-end.
OPENAI_COMPATIBLE = ("ollama", "openai", "azure_openai")


def _slug(ref: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", (ref or "").upper()).strip("_")


def resolve_credential(deployment: ProviderDeployment,
                       settings: Settings) -> ProviderCredential:
    """Resolve a deployment's endpoint + secret from the gateway's own store.

    Never falls back to vendor-SDK env auto-resolution — only the explicit
    `AIG_SECRET__*` / `AIG_PROVIDER_KEY__*` scheme documented on this module."""
    provider = deployment.provider
    ref = deployment.endpoint_vault_ref or ""
    provider_key_env = os.environ.get(f"AIG_PROVIDER_KEY__{provider.upper()}")

    base_url: str | None = None
    api_key: str | None = None
    api_version: str | None = None

    if ref.startswith("http://") or ref.startswith("https://"):
        base_url = ref
        api_key = provider_key_env
    elif ref:
        raw = os.environ.get(f"AIG_SECRET__{_slug(ref)}")
        if raw:
            raw = raw.strip()
            if raw.startswith("{"):
                try:
                    doc = json.loads(raw)
                    base_url = doc.get("base_url")
                    api_key = doc.get("api_key")
                    api_version = doc.get("api_version")
                except json.JSONDecodeError:
                    api_key = raw
            elif "|" in raw:
                base_url, api_key = (part.strip() or None for part in raw.split("|", 1))
            else:
                api_key = raw
        if api_key is None:
            api_key = provider_key_env

    # Provider-specific defaults for anything unresolved.
    if provider == "ollama":
        base_url = base_url or settings.ollama_base_url
        api_key = api_key or "ollama"
    elif provider == "anthropic":
        base_url = base_url or ANTHROPIC_DEFAULT_BASE_URL
    elif provider == "azure_openai":
        api_version = api_version or getattr(
            settings, "azure_openai_api_version", "2024-06-01")

    return ProviderCredential(base_url=base_url, api_key=api_key,
                              api_version=api_version)


class ProviderRegistry:
    """`ProviderClient` that dispatches each call to the deployment's provider
    adapter, caching one adapter instance per resolved (provider, endpoint,
    credential) so connection pools are reused across requests."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._timeout = float(getattr(settings, "ollama_request_timeout_s", 120.0))
        self._adapters: dict[tuple, object] = {}

    # ------------------------------------------------------------------ factory

    def _adapter_for(self, deployment: ProviderDeployment):
        provider = deployment.provider
        if provider in UNRUNNABLE_PROVIDERS:
            raise ProviderNotConfigured(
                f"provider {provider!r} is accepted by the admin/config layer but "
                f"its execution path requires real cloud-credential wiring "
                f"({'AWS SigV4' if provider == 'bedrock' else 'GCP ADC'}) that is "
                f"not present in this deployment; configure a runnable provider "
                f"(ollama/openai/azure_openai/anthropic) for live traffic"
            )
        cred = resolve_credential(deployment, self._settings)
        key = (provider, cred.base_url, cred.api_key, cred.api_version)
        adapter = self._adapters.get(key)
        if adapter is not None:
            return adapter

        if provider == "ollama":
            adapter = OpenAICompatibleProvider(
                cred.base_url or self._settings.ollama_base_url,
                timeout_s=self._timeout, api_key=cred.api_key or "ollama",
                label="ollama", auth_style="bearer")
        elif provider == "openai":
            adapter = OpenAICompatibleProvider(
                cred.base_url or "https://api.openai.com/v1",
                timeout_s=self._timeout, api_key=cred.api_key,
                label="openai", auth_style="bearer")
        elif provider == "azure_openai":
            if not cred.base_url:
                raise ProviderNotConfigured(
                    "azure_openai deployment has no endpoint; set endpoint_vault_ref "
                    "to the Azure resource URL or a secret carrying base_url")
            adapter = OpenAICompatibleProvider(
                cred.base_url, timeout_s=self._timeout, api_key=cred.api_key,
                label="azure_openai", auth_style="api-key",
                api_version=cred.api_version)
        elif provider == "anthropic":
            adapter = AnthropicProvider(
                cred.api_key, base_url=cred.base_url or ANTHROPIC_DEFAULT_BASE_URL,
                timeout_s=self._timeout)
        else:
            raise ProviderNotConfigured(f"unknown provider type {provider!r}")

        self._adapters[key] = adapter
        return adapter

    # ------------------------------------------------------------------ ProviderClient

    async def complete(self, deployment: ProviderDeployment,
                       request: ProviderRequest) -> ProviderResult:
        return await self._adapter_for(deployment).complete(deployment, request)

    def stream(self, deployment: ProviderDeployment,
               request: ProviderRequest) -> AsyncIterator[dict]:
        # Resolve eagerly so a config error surfaces before the SSE generator is
        # consumed; the adapter's own stream() is returned unchanged.
        return self._adapter_for(deployment).stream(deployment, request)

    async def embed(self, deployment: ProviderDeployment, model: str,
                    inputs: list[str]) -> tuple[list[list[float]], int]:
        return await self._adapter_for(deployment).embed(deployment, model, inputs)

    async def aclose(self) -> None:
        for adapter in self._adapters.values():
            aclose = getattr(adapter, "aclose", None)
            if aclose is not None:
                await aclose()
        self._adapters.clear()
