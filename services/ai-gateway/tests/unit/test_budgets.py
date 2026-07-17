"""Budgets: AC-2, AC-3, AC-13 (unit variant), AC-14, BR-3/9/12, degradation,
threshold exactly-once, reservation expiry."""

from __future__ import annotations

import asyncio

import pytest

from app.domain.entities import Budget
from app.domain.errors import DependencyUnavailable
from app.domain.ports import LedgerUnavailable
from tests.conftest import (
    CHAT_BODY,
    TENANT_A,
    WORKSPACE,
    dp_headers,
    ledger_key_for,
    mint_key,
    seed_default_deployments,
)


async def _create_budget(container, *, scope_type: str, scope_ref: str,
                         window: str = "monthly", limit_usd: float = 1.0,
                         degrade_pct: int = 95, tenant_id: str = TENANT_A) -> Budget:
    from app.utils import uuid7

    now = container.clock.now()
    b = Budget(id=str(uuid7()), tenant_id=tenant_id, scope_type=scope_type,
               scope_ref=scope_ref, window=window, limit_usd=limit_usd,
               degrade_pct=degrade_pct, created_at=now, updated_at=now)
    async with container.uow_factory(tenant_id) as uow:
        await uow.budgets.add(b)
        await uow.commit()
    return b


async def _seed_spend(container, budget: Budget, cents: int) -> None:
    key = ledger_key_for(budget.id, budget.window, container.clock)
    await container.ledger.settle(key, "seed", cents)


async def test_ac2_workspace_budget_exhausted_402(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    budget = await _create_budget(container, scope_type="workspace",
                                  scope_ref=WORKSPACE, limit_usd=1.50)
    await _seed_spend(container, budget, 150)  # 100%

    r = await client.post(
        "/v1/chat/completions", json=CHAT_BODY,
        headers=dp_headers(secret, **{"x-windrose-workspace-id": WORKSPACE}),
    )
    assert r.status_code == 402, r.text
    err = r.json()["error"]
    assert err["code"] == "BUDGET_EXHAUSTED"
    assert err["details"]["scope_type"] == "workspace"
    assert err["details"]["window"] == "monthly"
    assert err["details"]["reset_at"]
    assert "workspace" in err["message"] and "Resets" in err["message"]
    assert container.provider_client.calls == []  # no provider call

    span = container.tracer.spans_named("chat")[-1]
    assert span.attributes["windrose.rejected_stage"] == "budget_preflight"


async def test_br9_most_specific_exhausted_scope_named(client, container):
    """Tenant AND workspace both exhausted → error names workspace."""
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    t = await _create_budget(container, scope_type="tenant", scope_ref=TENANT_A,
                             limit_usd=1.0)
    w = await _create_budget(container, scope_type="workspace", scope_ref=WORKSPACE,
                             limit_usd=1.0)
    await _seed_spend(container, t, 100)
    await _seed_spend(container, w, 100)
    r = await client.post(
        "/v1/chat/completions", json=CHAT_BODY,
        headers=dp_headers(secret, **{"x-windrose-workspace-id": WORKSPACE}),
    )
    assert r.status_code == 402
    assert r.json()["error"]["details"]["scope_type"] == "workspace"


async def test_br12_default_tenant_budget_applies(client, container):
    """A tenant with zero configured budgets still hits the platform default."""
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    key = ledger_key_for(f"default-{TENANT_A}-daily", "daily", container.clock)
    await container.ledger.settle(key, "seed",
                                  int(container.settings.default_tenant_budget_daily_usd
                                      * 100))
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 402
    assert r.json()["error"]["details"]["scope_type"] == "tenant"


async def test_degradation_serves_lowest_rung(client, container):
    """AIG-FR-007: ≥ degrade_pct → rung 0 + x-windrose-degraded: budget."""
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    budget = await _create_budget(container, scope_type="tenant", scope_ref=TENANT_A,
                                  limit_usd=1.0, degrade_pct=95)
    await _seed_spend(container, budget, 96)  # 96% — degrading, not exhausted
    r = await client.post(
        "/v1/chat/completions", json={**CHAT_BODY, "model": "frontier"},
        headers=dp_headers(secret),
    )
    assert r.status_code == 200, r.text
    assert r.headers["x-windrose-rung"] == "0"
    assert r.headers["x-windrose-degraded"] == "budget"


async def test_escalation_denied_while_degraded(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    budget = await _create_budget(container, scope_type="tenant", scope_ref=TENANT_A,
                                  limit_usd=1.0)
    r1 = await client.post("/v1/chat/completions", json=CHAT_BODY,
                           headers=dp_headers(secret))
    prior_id = r1.headers["x-windrose-request-id"]
    await _seed_spend(container, budget, 96)
    r2 = await client.post(
        "/v1/chat/completions", json=CHAT_BODY,
        headers=dp_headers(secret, **{"x-windrose-escalate": "true",
                                      "x-windrose-prior-request-id": prior_id}),
    )
    assert r2.status_code == 403
    assert r2.json()["error"]["code"] == "LADDER_CAP"


async def test_ac3_threshold_events_exactly_once(container):
    """Crossing 95% emits exactly one budget.threshold event, even when two
    settlements race across the boundary."""
    budget = await _create_budget(container, scope_type="tenant",
                                  scope_ref=TENANT_A, limit_usd=1.0)
    engine = container.budget_engine
    windows = await engine.governing_windows(
        TENANT_A, __import__("app.domain.entities", fromlist=["Attribution"]).Attribution(),
        "user-1", "key-1", "chat", "UTC",
    )
    windows = [gw for gw in windows if gw.budget.id == budget.id]
    await container.ledger.settle(windows[0].ledger_key, "seed", 90)

    p1 = await engine.preflight(windows, 4)
    p2 = await engine.preflight(windows, 4)
    await asyncio.gather(engine.settle(p1, 4), engine.settle(p2, 4))

    events = container.bus.events_of_type("budget.threshold")
    pct95 = [e for e in events if e["payload"]["pct"] == 95]
    assert len(pct95) == 1
    assert pct95[0]["payload"]["scope_type"] == "tenant"


async def test_exhaustion_event_on_100(container):
    budget = await _create_budget(container, scope_type="tenant",
                                  scope_ref=TENANT_A, limit_usd=0.10)
    engine = container.budget_engine
    from app.domain.entities import Attribution

    windows = [gw for gw in await engine.governing_windows(
        TENANT_A, Attribution(), "u", "k", "chat", "UTC") if gw.budget.id == budget.id]
    p = await engine.preflight(windows, 10)
    await engine.settle(p, 10)
    assert len(container.bus.events_of_type("budget.exhausted")) == 1


async def test_br3_concurrent_reservations_cannot_overcommit(container):
    from app.domain.entities import Attribution
    from app.domain.errors import BudgetExhausted

    budget = await _create_budget(container, scope_type="tenant",
                                  scope_ref=TENANT_A, limit_usd=0.10)
    engine = container.budget_engine
    windows = [gw for gw in await engine.governing_windows(
        TENANT_A, Attribution(), "u", "k", "chat", "UTC") if gw.budget.id == budget.id]
    await engine.preflight(windows, 7)  # holds 7 of 10 cents
    with pytest.raises(BudgetExhausted):
        await engine.preflight(windows, 7)  # 7 + 7 > 10 → reservation fails


async def test_reservation_expiry_frees_budget(container, clock):
    from app.domain.entities import Attribution
    from app.domain.errors import BudgetExhausted

    budget = await _create_budget(container, scope_type="tenant",
                                  scope_ref=TENANT_A, limit_usd=0.10)
    engine = container.budget_engine
    windows = [gw for gw in await engine.governing_windows(
        TENANT_A, Attribution(), "u", "k", "chat", "UTC") if gw.budget.id == budget.id]
    await engine.preflight(windows, 10)
    with pytest.raises(BudgetExhausted):
        await engine.preflight(windows, 5)
    clock.advance(seconds=181)  # AIG-FR-021: reservations expire after 180s
    assert (await engine.preflight(windows, 5)).reservations


async def test_ac14_judge_uses_system_budget_and_temp0(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container, classes=["judge"])
    r = await client.post(
        "/v1/chat/completions",
        json={**CHAT_BODY, "temperature": 0.9},
        headers=dp_headers(secret, request_class="judge",
                           **{"x-windrose-feature": "eval"}),
    )
    assert r.status_code == 200, r.text
    # temperature forced to 0 at the provider
    _, preq = container.provider_client.calls[-1]
    assert preq.temperature == 0.0
    # metering carries tenant attribution + feature: eval
    event = container.bus.on_topic("ai.token_usage.v1")[-1]
    assert event["tenant_id"] == TENANT_A
    assert event["payload"]["feature"] == "eval"
    # spend drew from the platform system budget, not the tenant default
    sys_key = ledger_key_for("default-system-daily", "daily", container.clock)
    spent, _ = await container.ledger.usage(sys_key)
    assert spent > 0
    tenant_key = ledger_key_for(f"default-{TENANT_A}-daily", "daily", container.clock)
    tenant_spent, _ = await container.ledger.usage(tenant_key)
    assert tenant_spent == 0


async def test_judge_never_degraded(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container, classes=["judge"])
    # exhaust the tenant default budget: judge must not care
    key = ledger_key_for(f"default-{TENANT_A}-daily", "daily", container.clock)
    await container.ledger.settle(key, "seed", 100_000)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret, request_class="judge"))
    assert r.status_code == 200


class BrokenLedger:
    async def usage(self, key):
        raise LedgerUnavailable("down")

    async def reserve(self, *a):
        raise LedgerUnavailable("down")

    async def settle(self, *a):
        raise LedgerUnavailable("down")

    async def release(self, *a):
        raise LedgerUnavailable("down")

    async def flag_once(self, *a):
        raise LedgerUnavailable("down")

    async def sweep_expired(self):
        raise LedgerUnavailable("down")


async def test_ac13_unit_fail_closed_when_ledger_down(settings, clock):
    """Unit variant of AC-13: both budget backends down → 503, never fail-open."""
    from app.container import build_container
    from app.main import create_app
    from tests.conftest import _noop_sleeper

    container = build_container(settings, mode="memory", clock=clock,
                                sleeper=_noop_sleeper, ledger=BrokenLedger())
    app = create_app(container)
    import httpx

    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/v1/chat/completions", json=CHAT_BODY,
                         headers=dp_headers(secret))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


async def test_ac13_unit_fallback_ledger(container, clock):
    """Primary down → fallback serves; alert callback fires."""
    from app.adapters.ledger import FallbackLedger, InMemoryLedger

    alerts = []
    fallback = FallbackLedger(BrokenLedger(), InMemoryLedger(clock),
                              on_fallback=lambda: alerts.append(1))
    assert await fallback.reserve("bud:x:2026-07-10", 100, 10, "r1") is True
    assert alerts

    with pytest.raises(DependencyUnavailable):
        both_down = FallbackLedger(BrokenLedger(), BrokenLedger())
        from app.domain.budgets import BudgetEngine

        engine = BudgetEngine(container.uow_factory, both_down, clock,
                              container.settings, container.emit_event)
        from app.domain.entities import Attribution

        windows = await engine.governing_windows(TENANT_A, Attribution(), "u", "k",
                                                 "chat", "UTC")
        await engine.preflight(windows, 1)
