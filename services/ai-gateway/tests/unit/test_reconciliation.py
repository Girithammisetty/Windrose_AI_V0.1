"""AC-16 (metering reconciliation drift alert) + AIG-FR-025 (spend anomaly)."""

from __future__ import annotations

from tests.conftest import (
    CHAT_BODY,
    TENANT_A,
    dp_headers,
    mint_key,
    seed_default_deployments,
)


async def test_ac16_reconciliation_alert_on_injected_gap(client, container):
    deps = await seed_default_deployments(container)
    _, secret = await mint_key(container)
    for _ in range(3):
        assert (await client.post("/v1/chat/completions", json=CHAT_BODY,
                                  headers=dp_headers(secret))).status_code == 200

    day = container.clock.now().date().isoformat()
    gateway_totals = container.usage_recorder.totals_for(day)
    dep_id = deps["fast-small"].id
    assert gateway_totals[dep_id] > 0

    # no drift → no alert
    drifts = await container.reconciler.reconcile(day, dict(gateway_totals))
    assert drifts == []
    assert not container.bus.events_of_type("usage.reconciliation_drift")

    # inject a synthetic gap: provider billed 10% more than we metered
    inflated = {dep_id: int(gateway_totals[dep_id] * 1.10)}
    drifts = await container.reconciler.reconcile(day, inflated)
    assert len(drifts) == 1
    assert drifts[0]["deployment_id"] == dep_id  # per-deployment attribution
    assert drifts[0]["drift_pct"] > 1.0
    alerts = container.bus.events_of_type("usage.reconciliation_drift")
    assert len(alerts) == 1


async def test_reconciliation_matches_provider_billing(client, container):
    """Metering completeness: gateway totals equal provider-billed tokens."""
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    await client.post("/v1/chat/completions", json=CHAT_BODY,
                      headers=dp_headers(secret))
    day = container.clock.now().date().isoformat()
    gateway_total = sum(container.usage_recorder.totals_for(day).values())
    provider_total = sum(container.provider_client.billed_tokens.values())
    assert gateway_total == provider_total > 0


async def test_spend_anomaly_detection(container, clock):
    detector = container.anomaly
    from datetime import timedelta

    # trailing 7 days: ~10 cents at this hour
    hour = clock.now().hour
    for d in range(1, 8):
        day = (clock.now() - timedelta(days=d)).date().isoformat()
        detector._buckets[(TENANT_A, day, hour)] = 10
    await detector.observe(TENANT_A, 20)  # 2x — no alert
    assert not container.bus.events_of_type("budget.anomaly")
    await detector.observe(TENANT_A, 20)  # cumulative 40 > 3×10 — alert once
    events = container.bus.events_of_type("budget.anomaly")
    assert len(events) == 1
    await detector.observe(TENANT_A, 20)
    assert len(container.bus.events_of_type("budget.anomaly")) == 1
