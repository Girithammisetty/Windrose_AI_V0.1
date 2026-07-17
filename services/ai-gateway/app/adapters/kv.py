"""KV + pub/sub adapters: in-memory (unit/dev) and Redis (integration/prod)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from app.utils import Clock


class InMemoryKV:
    """Clock-aware in-memory KV honoring TTLs (unit tier)."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self._data: dict[str, tuple[str, datetime | None]] = {}

    def _live(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and self.clock.now() >= expires:
            del self._data[key]
            return None
        return value

    def _expiry(self, ttl_seconds: int | None) -> datetime | None:
        return self.clock.now() + timedelta(seconds=ttl_seconds) if ttl_seconds else None

    async def get(self, key: str) -> str | None:
        return self._live(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        self._data[key] = (value, self._expiry(ttl_seconds))

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def delete_prefix(self, prefix: str) -> int:
        doomed = [k for k in self._data if k.startswith(prefix)]
        for k in doomed:
            del self._data[k]
        return len(doomed)

    async def incr(self, key: str, ttl_seconds: int | None = None) -> int:
        return await self.incrby(key, 1, ttl_seconds)

    async def incrby(self, key: str, amount: int, ttl_seconds: int | None = None) -> int:
        current = self._live(key)
        expires = self._data[key][1] if current is not None else self._expiry(ttl_seconds)
        value = int(current or 0) + amount
        self._data[key] = (str(value), expires)
        return value

    async def decr(self, key: str) -> int:
        return await self.incrby(key, -1)

    async def setnx(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        if self._live(key) is not None:
            return False
        self._data[key] = (value, self._expiry(ttl_seconds))
        return True


class RedisKV:
    """redis.asyncio adapter (prod / integration tier)."""

    def __init__(self, redis):
        self.r = redis

    async def get(self, key: str) -> str | None:
        value = await self.r.get(key)
        return value.decode() if isinstance(value, bytes) else value

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        await self.r.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self.r.delete(key)

    async def delete_prefix(self, prefix: str) -> int:
        count = 0
        async for key in self.r.scan_iter(match=f"{prefix}*"):
            await self.r.delete(key)
            count += 1
        return count

    async def incr(self, key: str, ttl_seconds: int | None = None) -> int:
        value = await self.r.incr(key)
        if ttl_seconds and value == 1:
            await self.r.expire(key, ttl_seconds)
        return value

    async def incrby(self, key: str, amount: int, ttl_seconds: int | None = None) -> int:
        value = await self.r.incrby(key, amount)
        if ttl_seconds and value == amount:
            await self.r.expire(key, ttl_seconds)
        return value

    async def decr(self, key: str) -> int:
        return await self.r.decr(key)

    async def setnx(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        ok = await self.r.set(key, value, nx=True, ex=ttl_seconds)
        return bool(ok)


class InMemoryInvalidationChannel:
    """`keyrev` pub/sub fake: replicas sharing this instance see invalidations
    immediately (AIG-FR-031)."""

    def __init__(self):
        self._subscribers: list[Callable[[str, str], Awaitable[None]]] = []

    def subscribe(self, callback: Callable[[str, str], Awaitable[None]]) -> None:
        self._subscribers.append(callback)

    async def publish(self, kind: str, ref: str) -> None:
        for cb in list(self._subscribers):
            await cb(kind, ref)


class RedisInvalidationChannel:
    """Redis pub/sub on the `keyrev` channel. `start()` spawns the listener
    task; publishes reach every gateway replica ≤ 30s (AIG-FR-031)."""

    CHANNEL = "keyrev"

    def __init__(self, redis):
        self.r = redis
        self._subscribers: list[Callable[[str, str], Awaitable[None]]] = []
        self._task: asyncio.Task | None = None

    def subscribe(self, callback: Callable[[str, str], Awaitable[None]]) -> None:
        self._subscribers.append(callback)

    async def publish(self, kind: str, ref: str) -> None:
        await self.r.publish(self.CHANNEL, f"{kind}:{ref}")

    async def start(self) -> None:
        pubsub = self.r.pubsub()
        await pubsub.subscribe(self.CHANNEL)

        async def listen():
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                kind, _, ref = data.partition(":")
                for cb in list(self._subscribers):
                    await cb(kind, ref)

        self._task = asyncio.create_task(listen())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
