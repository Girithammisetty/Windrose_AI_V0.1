"""Spend kill-switch (freeze) — instant operator halt of AI spend (P2).

The hierarchical budgets (budgets.py) are rolling daily/monthly USD windows with
graceful degrade. A freeze is different: an operator's out-of-band, instant HARD
stop of all AI spend for a scope — platform-wide or one tenant — for incident
response (runaway cost, a compromised tenant/key). It is checked at the very top
of the data-plane pipeline, before admission/provider, and rejects with
``SpendFrozen`` (402) until an operator clears it. Independent of the budget window.

Scopes: ``"platform"`` (freezes every tenant) and ``"tenant:<tenant_id>"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.domain.errors import SpendFrozen, ValidationFailed

PLATFORM = "platform"


def tenant_scope(tenant_id: str) -> str:
    return f"tenant:{tenant_id}"


@dataclass(frozen=True, slots=True)
class Freeze:
    scope: str
    reason: str
    set_by: str
    set_at: str  # ISO-8601


@runtime_checkable
class FreezeStore(Protocol):
    async def get(self, scope: str) -> Freeze | None: ...
    async def put(self, freeze: Freeze) -> None: ...
    async def delete(self, scope: str) -> bool: ...
    async def list(self) -> list[Freeze]: ...


class SpendGuard:
    def __init__(self, store: FreezeStore) -> None:
        self._store = store

    async def active_for(self, tenant_id: str) -> Freeze | None:
        """The freeze in effect for a tenant's request, if any — platform freeze
        takes precedence over a tenant-specific one."""
        platform = await self._store.get(PLATFORM)
        if platform is not None:
            return platform
        return await self._store.get(tenant_scope(tenant_id))

    async def check(self, tenant_id: str) -> None:
        """Raise SpendFrozen if a platform or tenant freeze is active. Called on the
        hot path before any provider spend."""
        fz = await self.active_for(tenant_id)
        if fz is not None:
            raise SpendFrozen(
                f"AI spend is frozen ({fz.scope}): {fz.reason}",
                details={"scope": fz.scope, "reason": fz.reason,
                         "frozen_by": fz.set_by, "frozen_at": fz.set_at})

    async def freeze(self, scope: str, *, reason: str, by: str, at: str) -> Freeze:
        if scope != PLATFORM and not scope.startswith("tenant:"):
            raise ValidationFailed(f"invalid freeze scope {scope!r}")
        if not (reason or "").strip():
            raise ValidationFailed("freeze requires a reason")
        fz = Freeze(scope=scope, reason=reason.strip(), set_by=by, set_at=at)
        await self._store.put(fz)
        return fz

    async def clear(self, scope: str) -> bool:
        return await self._store.delete(scope)

    async def list(self) -> list[Freeze]:
        return await self._store.list()
