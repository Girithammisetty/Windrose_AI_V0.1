"""Spend-freeze stores (P2 kill-switch). InMemory for unit/dev; Redis for the hot
path (a freeze must take effect instantly and be checked on every request)."""

from __future__ import annotations

from app.domain.freeze import Freeze

_KEY = "spend_freeze:"
_SET = "spend_freeze:scopes"


class InMemoryFreezeStore:
    def __init__(self) -> None:
        self._freezes: dict[str, Freeze] = {}

    async def get(self, scope: str) -> Freeze | None:
        return self._freezes.get(scope)

    async def put(self, freeze: Freeze) -> None:
        self._freezes[freeze.scope] = freeze

    async def delete(self, scope: str) -> bool:
        return self._freezes.pop(scope, None) is not None

    async def list(self) -> list[Freeze]:
        return list(self._freezes.values())


class RedisFreezeStore:
    """One hash per scope (``spend_freeze:<scope>``) + a set of active scopes for
    listing. Async redis client (same one the ledger uses)."""

    def __init__(self, redis) -> None:
        self._r = redis

    async def get(self, scope: str) -> Freeze | None:
        h = await self._r.hgetall(_KEY + scope)
        if not h:
            return None
        d = {(k.decode() if isinstance(k, bytes) else k):
             (v.decode() if isinstance(v, bytes) else v) for k, v in h.items()}
        return Freeze(scope=scope, reason=d.get("reason", ""),
                      set_by=d.get("set_by", ""), set_at=d.get("set_at", ""))

    async def put(self, freeze: Freeze) -> None:
        await self._r.hset(_KEY + freeze.scope, mapping={
            "reason": freeze.reason, "set_by": freeze.set_by, "set_at": freeze.set_at})
        await self._r.sadd(_SET, freeze.scope)

    async def delete(self, scope: str) -> bool:
        removed = await self._r.delete(_KEY + scope)
        await self._r.srem(_SET, scope)
        return bool(removed)

    async def list(self) -> list[Freeze]:
        scopes = await self._r.smembers(_SET)
        out: list[Freeze] = []
        for s in scopes:
            scope = s.decode() if isinstance(s, bytes) else s
            fz = await self.get(scope)
            if fz is not None:
                out.append(fz)
        return out
