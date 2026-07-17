"""Budget gate (usage.events.v1: budget.exhausted / restored, §6).

When the ``inference_minutes`` meter is exhausted for a tenant, new submissions
are rejected 429 until ``budget.restored``. The real gate is a Redis flag; the
in-memory gate is the unit/dev double.
"""

from __future__ import annotations


class InMemoryBudgetGate:
    def __init__(self) -> None:
        self._exhausted: set[str] = set()

    async def is_exhausted(self, tenant_id: str) -> bool:
        return tenant_id in self._exhausted

    async def set_exhausted(self, tenant_id: str, exhausted: bool) -> None:
        if exhausted:
            self._exhausted.add(tenant_id)
        else:
            self._exhausted.discard(tenant_id)


class RedisBudgetGate:
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        from windrose_common.redisx import build_redis

        self._redis = build_redis(redis_url)

    @staticmethod
    def _key(tenant_id: str) -> str:
        return f"budget:exhausted:{tenant_id}:inference_minutes"

    async def is_exhausted(self, tenant_id: str) -> bool:
        return bool(await self._redis.exists(self._key(tenant_id)))

    async def set_exhausted(self, tenant_id: str, exhausted: bool) -> None:
        if exhausted:
            await self._redis.set(self._key(tenant_id), "1")
        else:
            await self._redis.delete(self._key(tenant_id))

    async def aclose(self) -> None:
        await self._redis.aclose()
