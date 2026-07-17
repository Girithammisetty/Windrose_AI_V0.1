"""Tenant-scoped semantic cache (AIG-FR-040..043, BR-6/15).

Exact tier: KV (Redis in prod) keyed `cache:{tenant}:{prompt_hash}:{context_hash}`.
Semantic tier: embedding similarity within the same tenant + context_hash
(pgvector in sql mode; in-memory cosine in unit mode). Tenant is a hard
component of both tiers — never cross-tenant."""

from __future__ import annotations

import json

from app.config import Settings
from app.domain.entities import CacheEntry
from app.domain.ports import KV, Embedder, UowFactory
from app.utils import Clock, estimate_tokens, sha256_hex, uuid7


def normalize_messages(messages: list[dict]) -> str:
    return json.dumps(
        [{"role": m.get("role"), "content": m.get("content")} for m in messages],
        sort_keys=True,
        separators=(",", ":"),
    )


def prompt_hash_of(messages: list[dict]) -> str:
    return sha256_hex(normalize_messages(messages))


def context_hash_of(*, model_alias: str, request_class: str, tools: list | None,
                    temperature: float, system_prompt_version: str | None,
                    guardrail_policy_version: int) -> str:
    return sha256_hex(json.dumps({
        "model_alias": model_alias,
        "request_class": request_class,
        "tools": tools or [],
        "temperature": temperature,
        "system_prompt_version": system_prompt_version,
        "guardrail_policy_version": guardrail_policy_version,
    }, sort_keys=True))


class SemanticCache:
    def __init__(self, kv: KV, embedder: Embedder, uow_factory: UowFactory,
                 clock: Clock, settings: Settings):
        self.kv = kv
        self.embedder = embedder
        self.uow_factory = uow_factory
        self.clock = clock
        self.settings = settings

    # ------------------------------------------------------------------ eligibility

    def eligible(self, *, request_class: str, temperature: float, stream: bool,
                 tools: list | None, ttl_seconds: int) -> bool:
        """AIG-FR-042: no caching for judge, temp > 0.2, stream+tools, ttl=0."""
        if ttl_seconds <= 0:
            return False
        if request_class == "judge":
            return False
        if temperature > self.settings.cache_max_temperature:
            return False
        if stream and tools:
            return False
        return True

    def ttl_for(self, tenant_ttl: int | None) -> int:
        ttl = self.settings.cache_ttl_seconds_default if tenant_ttl is None else tenant_ttl
        return max(0, min(ttl, self.settings.cache_ttl_seconds_max))

    # ------------------------------------------------------------------ lookup/store

    def _exact_key(self, tenant_id: str, prompt_hash: str, context_hash: str) -> str:
        return f"cache:{tenant_id}:{prompt_hash}:{context_hash}"

    async def lookup(self, tenant_id: str, messages: list[dict], prompt_hash: str,
                     context_hash: str) -> tuple[str, dict | None]:
        """Returns (tier, response): tier in hit_exact | hit_semantic | miss."""
        raw = await self.kv.get(self._exact_key(tenant_id, prompt_hash, context_hash))
        if raw is not None:
            return "hit_exact", json.loads(raw)
        # BR-15: semantic tier only after an exact miss and for prompts ≥ 64 tokens
        prompt_text = normalize_messages(messages)
        if estimate_tokens(prompt_text) < self.settings.cache_min_prompt_tokens:
            return "miss", None
        threshold = max(self.settings.cache_similarity_threshold,
                        self.settings.cache_similarity_floor)
        vector = await self.embedder.embed(prompt_text)
        async with self.uow_factory(tenant_id) as uow:
            entry = await uow.cache_entries.search(
                context_hash, vector, threshold, self.clock.now()
            )
        if entry is not None:
            return "hit_semantic", entry.response
        return "miss", None

    async def store(self, tenant_id: str, messages: list[dict], prompt_hash: str,
                    context_hash: str, response: dict, ttl_seconds: int,
                    workspace_id: str | None) -> None:
        from datetime import timedelta

        await self.kv.set(
            self._exact_key(tenant_id, prompt_hash, context_hash),
            json.dumps(response),
            ttl_seconds=ttl_seconds,
        )
        prompt_text = normalize_messages(messages)
        if estimate_tokens(prompt_text) < self.settings.cache_min_prompt_tokens:
            return
        vector = await self.embedder.embed(prompt_text)
        entry = CacheEntry(
            id=str(uuid7()),
            tenant_id=tenant_id,
            prompt_hash=prompt_hash,
            context_hash=context_hash,
            embedding=vector,
            response=response,
            workspace_id=workspace_id,
            expires_at=self.clock.now() + timedelta(seconds=ttl_seconds),
            created_at=self.clock.now(),
        )
        async with self.uow_factory(tenant_id) as uow:
            await uow.cache_entries.add(entry)
            await uow.commit()

    async def invalidate(self, tenant_id: str, workspace_id: str | None = None) -> int:
        """Tenant/workspace invalidation (AIG-FR-043, BR-18)."""
        await self.kv.delete_prefix(f"cache:{tenant_id}:")
        async with self.uow_factory(tenant_id) as uow:
            purged = await uow.cache_entries.purge(workspace_id)
            await uow.commit()
        return purged
