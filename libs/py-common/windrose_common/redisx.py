"""Thin real Redis helpers (redis-py asyncio): connection factory, a consumer
dedup store (MASTER-FR-032, 24h TTL), and a small JSON projection reader used by
the OPA client to load the authz input projection.
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

DEDUP_TTL_SECONDS = 24 * 3600


def build_redis(url: str = "redis://localhost:6379/0") -> aioredis.Redis:
    return aioredis.from_url(url, encoding="utf-8", decode_responses=True)


class RedisDedupStore:
    """Consumer dedup with a 24h TTL. ``already_processed`` is a read-only EXISTS
    check; ``mark_processed`` is a TTL'd SET written only after handler effects
    are durable (handle-then-mark) so a mid-handler crash leaves the event
    un-deduped for idempotent redelivery — exactly-once effect."""

    def __init__(self, redis: aioredis.Redis, *, ttl_seconds: int = DEDUP_TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def _key(tenant_id: str, event_id: str) -> str:
        return f"dedup:{tenant_id}:{event_id}"

    async def already_processed(self, tenant_id: str, event_id: str) -> bool:
        return bool(await self._redis.exists(self._key(tenant_id, event_id)))

    async def mark_processed(self, tenant_id: str, event_id: str) -> None:
        await self._redis.set(self._key(tenant_id, event_id), "1", ex=self._ttl)

    async def claim(self, tenant_id: str, event_id: str) -> bool:
        """Atomic claim (SET NX) — returns True if this caller won the claim.
        Useful where check-then-mark must be race-free within one worker pool."""
        return bool(
            await self._redis.set(self._key(tenant_id, event_id), "1", nx=True, ex=self._ttl)
        )


class RedisProjection:
    """Reads/writes JSON projection entries (e.g. the authz input the OPA client
    posts). Real Redis GET/SET with JSON encoding."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> dict[str, Any] | None:
        raw = await self._redis.get(key)
        return json.loads(raw) if raw else None

    async def put(self, key: str, value: dict[str, Any], ttl_seconds: int | None = None) -> None:
        await self._redis.set(key, json.dumps(value), ex=ttl_seconds)
