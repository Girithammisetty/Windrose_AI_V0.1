"""Kill switch (ART-FR-063, AC-9): Postgres-durable, Redis-pushed. Propagation
≤5s via a Redis set (`ar:kill:set`) + pub/sub (`ar:kill`). The store persists the
switch; this registry is the hot-path lookup + fan-out."""

from __future__ import annotations

from app.domain.entities import KillSwitch

KILL_SET = "ar:kill:set"
KILL_CHANNEL = "ar:kill"


def _members(ks: KillSwitch) -> list[str]:
    """The set keys a kill covers: agent-wide, version-wide, and version×tenant."""
    keys = [f"agent:{ks.agent_key}"]
    if ks.version is not None:
        keys.append(f"agent_version:{ks.agent_key}:{ks.version}")
        if ks.tenant_id:
            keys.append(f"avt:{ks.agent_key}:{ks.version}:{ks.tenant_id}")
    return keys


def lookup_keys(agent_key: str, version: int, tenant_id: str) -> list[str]:
    return [
        f"agent:{agent_key}",
        f"agent_version:{agent_key}:{version}",
        f"avt:{agent_key}:{version}:{tenant_id}",
    ]


class RedisKillRegistry:
    def __init__(self, redis_url: str) -> None:
        from windrose_common.redisx import build_redis

        self._redis = build_redis(redis_url)

    async def is_killed(self, *, agent_key: str, version: int, tenant_id: str) -> bool:
        keys = lookup_keys(agent_key, version, tenant_id)
        hits = await self._redis.smismember(KILL_SET, keys)
        return any(bool(h) for h in hits)

    async def set_kill(self, ks: KillSwitch) -> None:
        members = _members(ks)
        await self._redis.sadd(KILL_SET, *members)
        await self._redis.publish(KILL_CHANNEL, f"kill:{ks.kill_id}")

    async def clear_kill(self, ks: KillSwitch) -> None:
        members = _members(ks)
        if members:
            await self._redis.srem(KILL_SET, *members)
        await self._redis.publish(KILL_CHANNEL, f"unkill:{ks.kill_id}")

    async def aclose(self) -> None:
        await self._redis.aclose()


class InMemoryKillRegistry:
    """Unit-tier double."""

    def __init__(self) -> None:
        self._set: set[str] = set()

    async def is_killed(self, *, agent_key: str, version: int, tenant_id: str) -> bool:
        return any(k in self._set for k in lookup_keys(agent_key, version, tenant_id))

    async def set_kill(self, ks: KillSwitch) -> None:
        self._set.update(_members(ks))

    async def clear_kill(self, ks: KillSwitch) -> None:
        self._set.difference_update(_members(ks))
