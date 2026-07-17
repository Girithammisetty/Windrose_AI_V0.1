"""Session-scope working memory (MEM-FR-002, BR-3, US-12, AC-12).

Session scope lives ONLY in Redis (no embeddings, no pgvector). Keys:
``mem:sess:{tenant}:{session_id}`` — a hash of entry_id -> JSON value, with a
TTL of session lifetime + 1h. ``RedisSessionStore`` is the real runtime adapter
over the shared ``windrose_common`` connection; ``InMemorySessionStore`` is the
unit-tier double.
"""

from __future__ import annotations

import json


def _key(tenant_id: str, session_id: str) -> str:
    return f"mem:sess:{tenant_id}:{session_id}"


class RedisSessionStore:
    def __init__(self, redis_url: str = "redis://localhost:6379/0", *, ttl_seconds: int = 32400):
        from windrose_common.redisx import build_redis

        self._redis = build_redis(redis_url)
        self._ttl = ttl_seconds

    async def put(self, tenant_id: str, session_id: str, entry_id: str, value: dict) -> None:
        key = _key(tenant_id, session_id)
        await self._redis.hset(key, entry_id, json.dumps(value, default=str))
        await self._redis.expire(key, self._ttl)

    async def list(self, tenant_id: str, session_id: str) -> list[dict]:
        raw = await self._redis.hgetall(_key(tenant_id, session_id))
        return [json.loads(v) for v in raw.values()]

    async def wipe(self, tenant_id: str, session_id: str) -> int:
        return int(await self._redis.delete(_key(tenant_id, session_id)))

    async def scan_subject(self, tenant_id: str, subject_id: str) -> int:
        """Count session hashes referencing the subject (erasure probe, AC-7)."""
        hits = 0
        async for key in self._redis.scan_iter(match=f"mem:sess:{tenant_id}:*"):
            values = await self._redis.hgetall(key)
            if any(subject_id in v for v in values.values()):
                hits += 1
        return hits

    async def purge_subject(self, tenant_id: str, subject_id: str) -> int:
        """Delete session hashes referencing the subject (erasure step 4)."""
        purged = 0
        async for key in self._redis.scan_iter(match=f"mem:sess:{tenant_id}:*"):
            values = await self._redis.hgetall(key)
            if any(subject_id in v for v in values.values()):
                await self._redis.delete(key)
                purged += 1
        return purged

    async def aclose(self) -> None:
        await self._redis.aclose()


class InMemorySessionStore:
    """Unit-tier double — NOT wired into the real runtime container."""

    def __init__(self):
        self._data: dict[str, dict[str, dict]] = {}

    async def put(self, tenant_id: str, session_id: str, entry_id: str, value: dict) -> None:
        self._data.setdefault(_key(tenant_id, session_id), {})[entry_id] = value

    async def list(self, tenant_id: str, session_id: str) -> list[dict]:
        return list(self._data.get(_key(tenant_id, session_id), {}).values())

    async def wipe(self, tenant_id: str, session_id: str) -> int:
        return 1 if self._data.pop(_key(tenant_id, session_id), None) is not None else 0

    async def scan_subject(self, tenant_id: str, subject_id: str) -> int:
        prefix = f"mem:sess:{tenant_id}:"
        return sum(
            1 for key, entries in self._data.items()
            if key.startswith(prefix)
            and any(subject_id in json.dumps(v, default=str) for v in entries.values())
        )

    async def purge_subject(self, tenant_id: str, subject_id: str) -> int:
        prefix = f"mem:sess:{tenant_id}:"
        keys = [
            key for key, entries in self._data.items()
            if key.startswith(prefix)
            and any(subject_id in json.dumps(v, default=str) for v in entries.values())
        ]
        for k in keys:
            self._data.pop(k, None)
        return len(keys)
