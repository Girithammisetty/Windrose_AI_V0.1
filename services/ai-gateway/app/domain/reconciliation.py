"""Daily metering reconciliation (NFR: 100% metering completeness, AC-16) and
spend anomaly detection (AIG-FR-025)."""

from __future__ import annotations

from app.config import Settings
from app.utils import Clock


class UsageRecorder:
    """Per-deployment daily token totals as metered by the gateway. In prod the
    reconciliation job reads usage-service/ClickHouse; this in-process recorder
    keeps the contract testable (wave-1)."""

    def __init__(self):
        self._totals: dict[tuple[str, str], int] = {}  # (deployment_id, day) -> tokens

    def observe(self, deployment_id: str, day: str, tokens: int) -> None:
        key = (deployment_id, day)
        self._totals[key] = self._totals.get(key, 0) + tokens

    def totals_for(self, day: str) -> dict[str, int]:
        return {dep: t for (dep, d), t in self._totals.items() if d == day}


class UsageReconciler:
    """Compares gateway-metered totals against provider-reported usage; drift
    beyond the alert threshold emits `usage.reconciliation_drift` with
    per-deployment attribution (AC-16)."""

    def __init__(self, recorder: UsageRecorder, settings: Settings, emit_event):
        self.recorder = recorder
        self.settings = settings
        self.emit_event = emit_event

    async def reconcile(self, day: str, provider_report: dict[str, int]) -> list[dict]:
        gateway = self.recorder.totals_for(day)
        drifts: list[dict] = []
        for deployment_id in sorted(set(gateway) | set(provider_report)):
            metered = gateway.get(deployment_id, 0)
            billed = provider_report.get(deployment_id, 0)
            base = max(billed, 1)
            drift_pct = abs(billed - metered) / base * 100
            if drift_pct > self.settings.reconciliation_drift_alert_pct:
                drifts.append({
                    "deployment_id": deployment_id,
                    "day": day,
                    "metered_tokens": metered,
                    "provider_tokens": billed,
                    "drift_pct": round(drift_pct, 3),
                })
        if drifts:
            await self.emit_event(self.settings.platform_tenant_id,
                                  "usage.reconciliation_drift",
                                  {"day": day, "deployments": drifts})
        return drifts


class SpendAnomalyDetector:
    """>3× trailing-7-day same-hour spend rate emits `budget.anomaly`
    (AIG-FR-025). In-memory hourly buckets; ClickHouse-backed in prod (TODO)."""

    def __init__(self, clock: Clock, settings: Settings, emit_event):
        self.clock = clock
        self.settings = settings
        self.emit_event = emit_event
        self._buckets: dict[tuple[str, str, int], int] = {}  # (tenant, day, hour) -> cents
        self._alerted: set[tuple[str, str, int]] = set()

    async def observe(self, tenant_id: str, cents: int) -> None:
        from datetime import timedelta

        now = self.clock.now()
        day, hour = now.date().isoformat(), now.hour
        key = (tenant_id, day, hour)
        self._buckets[key] = self._buckets.get(key, 0) + cents

        history = [
            self._buckets.get(
                (tenant_id, (now - timedelta(days=d)).date().isoformat(), hour), 0
            )
            for d in range(1, 8)
        ]
        observed_days = [h for h in history if h > 0]
        if not observed_days:
            return
        avg = sum(observed_days) / len(observed_days)
        if self._buckets[key] > self.settings.anomaly_multiplier * avg and key not in (
            self._alerted
        ):
            self._alerted.add(key)
            await self.emit_event(tenant_id, "budget.anomaly", {
                "tenant_id": tenant_id,
                "hour": f"{day}T{hour:02d}:00:00Z",
                "spend_usd": self._buckets[key] / 100,
                "trailing_avg_usd": avg / 100,
                "multiplier": round(self._buckets[key] / avg, 2),
            })
