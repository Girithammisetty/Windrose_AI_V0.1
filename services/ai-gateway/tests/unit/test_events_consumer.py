"""Consumed events (§6): tenant.provisioned / tenant.suspended /
budget.adjusted, with dedup (MASTER-FR-032)."""

from __future__ import annotations

import pytest

from app.events.envelope import make_envelope
from tests.conftest import TENANT_A, mint_key


def _identity_event(event_type: str, tenant_id: str = TENANT_A,
                    payload: dict | None = None) -> dict:
    return make_envelope(
        event_type=event_type, tenant_id=tenant_id,
        actor={"type": "service", "id": "identity-service"},
        resource_urn=f"wr:{tenant_id}:identity:tenant/{tenant_id}",
        payload=payload or {},
    )


async def test_tenant_provisioned_creates_defaults(container):
    await container.bus.publish("identity.events.v1", _identity_event(
        "tenant.provisioned",
        payload={"timezone": "Europe/Berlin", "cell_cloud": "gcp"},
    ))
    async with container.uow_factory(TENANT_A) as uow:
        budgets = await uow.budgets.for_scope("tenant", TENANT_A)
        assert {b.window for b in budgets} == {"daily", "monthly"}
        policy = await uow.policies.current()
        assert policy is not None and policy.version == 1
        cfg = await uow.tenant_configs.get(TENANT_A)
        assert cfg.timezone == "Europe/Berlin"
        assert cfg.cell_cloud == "gcp"


async def test_tenant_provisioned_is_idempotent(container):
    event = _identity_event("tenant.provisioned")
    await container.bus.publish("identity.events.v1", event)
    await container.bus.publish("identity.events.v1", event)  # replay, same id
    async with container.uow_factory(TENANT_A) as uow:
        budgets = await uow.budgets.for_scope("tenant", TENANT_A)
    assert len(budgets) == 2  # not duplicated


async def test_tenant_suspended_revokes_keys_and_drops_cache(container):
    from app.domain.errors import KeyInvalid

    key, secret = await mint_key(container)
    assert (await container.key_service.authenticate(secret)).id == key.id
    await container.bus.publish("identity.events.v1",
                                _identity_event("tenant.suspended"))
    with pytest.raises(KeyInvalid):  # ≤ 30s: invalidation is immediate
        await container.key_service.authenticate(secret)


async def test_budget_adjusted_reconciles_limit(container):
    from app.domain.entities import Budget
    from app.utils import uuid7

    b = Budget(id=str(uuid7()), tenant_id=TENANT_A, scope_type="tenant",
               scope_ref=TENANT_A, window="monthly", limit_usd=100.0,
               created_at=container.clock.now(), updated_at=container.clock.now())
    async with container.uow_factory(TENANT_A) as uow:
        await uow.budgets.add(b)
        await uow.commit()
    await container.bus.publish("usage.events.v1", make_envelope(
        event_type="budget.adjusted", tenant_id=TENANT_A,
        actor={"type": "service", "id": "usage-service"},
        resource_urn=f"wr:{TENANT_A}:ai:budget/{b.id}",
        payload={"budget_id": b.id, "limit_usd": 250.0},
    ))
    async with container.uow_factory(TENANT_A) as uow:
        refreshed = await uow.budgets.get(b.id)
    assert refreshed.limit_usd == 250.0
