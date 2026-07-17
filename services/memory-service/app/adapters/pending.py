"""Embedding-outage write queue (BR-2 / AC-11).

When the embeddings backend is unavailable a write that has already passed
injection screening + PII policy is parked here (never persisted unembedded).
A drain job retries it while the outage lasts and fails it past the ≤1h window.

``RedisPendingQueue`` is the real runtime adapter over the shared Redis
connection (BRD keyspace ``mem:pend``); ``InMemoryPendingQueue`` is the unit
double.
"""

from __future__ import annotations

import json


def _key(tenant_id: str) -> str:
    return f"mem:pend:{tenant_id}"


class RedisPendingQueue:
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        from windrose_common.redisx import build_redis

        self._redis = build_redis(redis_url)

    async def enqueue(self, entry: dict) -> None:
        await self._redis.hset(_key(entry["tenant_id"]), entry["id"],
                               json.dumps(entry, default=str))

    async def list_all(self, tenant_id: str) -> list[dict]:
        raw = await self._redis.hgetall(_key(tenant_id))
        return [json.loads(v) for v in raw.values()]

    async def remove(self, tenant_id: str, entry_id: str) -> None:
        await self._redis.hdel(_key(tenant_id), entry_id)

    async def aclose(self) -> None:
        await self._redis.aclose()


class InMemoryPendingQueue:
    """Unit-tier double — NOT wired into the real runtime container."""

    def __init__(self):
        self._data: dict[str, dict[str, dict]] = {}

    async def enqueue(self, entry: dict) -> None:
        self._data.setdefault(entry["tenant_id"], {})[entry["id"]] = dict(entry)

    async def list_all(self, tenant_id: str) -> list[dict]:
        return list(self._data.get(tenant_id, {}).values())

    async def remove(self, tenant_id: str, entry_id: str) -> None:
        self._data.get(tenant_id, {}).pop(entry_id, None)
