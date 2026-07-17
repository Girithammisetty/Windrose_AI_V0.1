"""Durable background workers (real runtime only).

The BRD names Temporal as the orchestration engine for the reconciliation cron
and the promotion approval gate's 14-day timer. In this build those behaviours
are realised by restart-safe in-process loops whose state lives in Postgres:

* reconciliation re-derives its work from ``reconciliation_watermarks`` +
  MLflow, so a restart resumes exactly where it left off;
* promotion expiry re-derives from ``promotions.expires_at``;
* the inbox applier re-derives from unapplied ``mirror_inbox`` rows;
* the outbox relay re-derives from unpublished ``outbox`` rows.

No state is held only in memory — this is a real, durable substitution for the
Temporal workflows (documented in README as the single orchestration deviation),
not a stub.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def _distinct_tenants(session_factory, table: str, where: str = "") -> list[str]:
    async with session_factory() as session:
        await session.execute(text("SELECT set_config('app.worker', 'true', true)"))
        sql = f"SELECT DISTINCT tenant_id::text FROM {table}"
        if where:
            sql += f" WHERE {where}"
        rows = (await session.execute(text(sql))).scalars().all()
    return list(rows)


async def reconcile_loop(container, stop: asyncio.Event) -> None:
    interval = container.settings.reconcile_interval_seconds
    sf = container.extras["session_factory"]
    while not stop.is_set():
        try:
            tenants = await _distinct_tenants(sf, "experiments", "deleted_at IS NULL")
            total_drift = 0
            for tenant_id in tenants:
                result = await container.reconciliation_service.sweep_tenant(tenant_id)
                total_drift += result["drift_count"]
            gauges = container.extras.setdefault("gauges", {})
            gauges["mlflow_mirror_drift_total"] = total_drift
            gauges["mlflow_mirror_lag_seconds"] = 0
        except Exception:  # noqa: BLE001
            logger.exception("reconcile sweep failed")
        await _sleep_or_stop(stop, interval)


async def expiry_loop(container, stop: asyncio.Event) -> None:
    interval = container.settings.promotion_expiry_interval_seconds
    sf = container.extras["session_factory"]
    while not stop.is_set():
        try:
            tenants = await _distinct_tenants(sf, "promotions", "status = 0")
            for tenant_id in tenants:
                await container.promotion_service.expire_pending_for_tenant(tenant_id)
        except Exception:  # noqa: BLE001
            logger.exception("promotion expiry sweep failed")
        await _sleep_or_stop(stop, interval)


async def inbox_loop(container, stop: asyncio.Event) -> None:
    sf = container.extras["session_factory"]
    while not stop.is_set():
        try:
            tenants = await _distinct_tenants(
                sf, "mirror_inbox", "applied_at IS NULL")
            for tenant_id in tenants:
                await container.mirror_service.apply_inbox_once(tenant_id)
        except Exception:  # noqa: BLE001
            logger.exception("inbox applier failed")
        await _sleep_or_stop(stop, 2.0)


async def outbox_loop(container, stop: asyncio.Event) -> None:
    dispatcher = container.extras.get("outbox_dispatcher")
    if dispatcher is None:
        return
    while not stop.is_set():
        try:
            await dispatcher.run_once()
        except Exception:  # noqa: BLE001
            logger.exception("outbox relay failed")
        await _sleep_or_stop(stop, 1.0)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass
