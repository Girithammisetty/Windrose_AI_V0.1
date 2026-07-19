"""Spend kill-switch (P2): instant operator freeze of AI spend, checked before any
provider call, independent of the rolling budget windows."""

from __future__ import annotations

import pytest

from app.adapters.freeze import InMemoryFreezeStore
from app.domain.errors import SpendFrozen, ValidationFailed
from app.domain.freeze import PLATFORM, SpendGuard, tenant_scope


def _guard():
    return SpendGuard(InMemoryFreezeStore())


async def test_no_freeze_allows_spend():
    await _guard().check("t-1")  # does not raise


async def test_tenant_freeze_blocks_only_that_tenant():
    g = _guard()
    await g.freeze(tenant_scope("t-1"), reason="runaway agent loop", by="op",
                  at="2026-07-19T00:00:00Z")
    with pytest.raises(SpendFrozen) as ei:
        await g.check("t-1")
    assert ei.value.details["scope"] == "tenant:t-1"
    assert "runaway agent loop" in str(ei.value)
    await g.check("t-2")  # a different tenant is unaffected


async def test_platform_freeze_blocks_every_tenant_and_takes_precedence():
    g = _guard()
    await g.freeze(tenant_scope("t-1"), reason="tenant", by="op", at="t")
    await g.freeze(PLATFORM, reason="incident: cost spike", by="op", at="t")
    for tid in ("t-1", "t-2", "t-anything"):
        with pytest.raises(SpendFrozen) as ei:
            await g.check(tid)
        assert ei.value.details["scope"] == "platform"  # platform precedence


async def test_clear_lifts_the_freeze():
    g = _guard()
    await g.freeze(tenant_scope("t-1"), reason="x", by="op", at="t")
    assert await g.clear(tenant_scope("t-1")) is True
    await g.check("t-1")  # cleared → allowed
    assert await g.clear(tenant_scope("t-1")) is False  # idempotent


async def test_freeze_requires_reason_and_valid_scope():
    g = _guard()
    with pytest.raises(ValidationFailed):
        await g.freeze(tenant_scope("t-1"), reason="  ", by="op", at="t")
    with pytest.raises(ValidationFailed):
        await g.freeze("bogus-scope", reason="x", by="op", at="t")


async def test_list_returns_active_freezes():
    g = _guard()
    await g.freeze(PLATFORM, reason="a", by="op", at="t")
    await g.freeze(tenant_scope("t-9"), reason="b", by="op", at="t")
    scopes = {f.scope for f in await g.list()}
    assert scopes == {PLATFORM, "tenant:t-9"}
