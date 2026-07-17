"""Consumed events (BRD 12 §6):

- identity.events.v1: tenant.provisioned → default tenant budgets, default
  guardrail policy, tenant config projection (timezone, cell_cloud);
  tenant.suspended → revoke all tenant keys ≤ 30s + drop tenant cache (BR-18).
- usage.events.v1: budget.adjusted → reconcile limits."""

from __future__ import annotations

import copy
import logging

from app.config import DEFAULT_GUARDRAIL_POLICY, Settings
from app.domain.cache import SemanticCache
from app.domain.entities import Budget, GuardrailPolicy, TenantConfig
from app.domain.keys import KeyService
from app.domain.ports import UowFactory
from app.utils import Clock, uuid7

logger = logging.getLogger(__name__)


class IdentityEventHandler:
    def __init__(self, uow_factory: UowFactory, dedup, keys: KeyService,
                 cache: SemanticCache, clock: Clock, settings: Settings):
        self.uow_factory = uow_factory
        self.dedup = dedup
        self.keys = keys
        self.cache = cache
        self.clock = clock
        self.settings = settings

    async def handle(self, envelope: dict) -> None:
        tenant_id = envelope["tenant_id"]
        if await self.dedup.seen(tenant_id, envelope["event_id"]):
            return
        event_type = envelope["event_type"]
        payload = envelope.get("payload") or {}
        if event_type == "tenant.provisioned":
            await self._provision(tenant_id, payload)
        elif event_type in ("tenant.suspended", "tenant.deleted"):
            await self.keys.revoke_all_for_tenant(tenant_id)
            await self.cache.invalidate(tenant_id)

    async def _provision(self, tenant_id: str, payload: dict) -> None:
        now = self.clock.now()
        async with self.uow_factory(tenant_id) as uow:
            existing = await uow.budgets.for_scope("tenant", tenant_id)
            if not existing:
                for window, limit in (
                    ("daily", self.settings.default_tenant_budget_daily_usd),
                    ("monthly", self.settings.default_tenant_budget_monthly_usd),
                ):
                    await uow.budgets.add(Budget(
                        id=str(uuid7()), tenant_id=tenant_id, scope_type="tenant",
                        scope_ref=tenant_id, window=window, limit_usd=limit,
                        degrade_pct=self.settings.default_degrade_pct,
                        created_at=now, updated_at=now,
                    ))
            if await uow.policies.current() is None:
                await uow.policies.put(GuardrailPolicy(
                    id=str(uuid7()), tenant_id=tenant_id,
                    policy=copy.deepcopy(DEFAULT_GUARDRAIL_POLICY), version=1,
                    created_at=now, updated_at=now,
                ))
            await uow.tenant_configs.put(TenantConfig(
                tenant_id=tenant_id,
                timezone=payload.get("timezone", "UTC"),
                cell_cloud=payload.get("cell_cloud"),
                created_at=now, updated_at=now,
            ))
            await uow.commit()


class UsageEventHandler:
    """usage.events.v1: budget.adjusted → reconcile limits (§6)."""

    def __init__(self, uow_factory: UowFactory, dedup, clock: Clock):
        self.uow_factory = uow_factory
        self.dedup = dedup
        self.clock = clock

    async def handle(self, envelope: dict) -> None:
        tenant_id = envelope["tenant_id"]
        if await self.dedup.seen(tenant_id, envelope["event_id"]):
            return
        if envelope["event_type"] != "budget.adjusted":
            return
        payload = envelope.get("payload") or {}
        budget_id = payload.get("budget_id")
        limit_usd = payload.get("limit_usd")
        if not budget_id or limit_usd is None:
            logger.warning("budget.adjusted missing budget_id/limit_usd; skipping")
            return
        async with self.uow_factory(tenant_id) as uow:
            budget = await uow.budgets.get(budget_id)
            if budget is not None:
                budget.limit_usd = float(limit_usd)
                budget.updated_at = self.clock.now()
                await uow.budgets.update(budget)
            await uow.commit()
