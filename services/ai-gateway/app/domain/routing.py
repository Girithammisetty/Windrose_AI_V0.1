"""Per-cloud affinity routing, circuit breaker, health probing, failover
(AIG-FR-004/008/009/009a/009b, BR-8)."""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

from app.config import Settings
from app.domain.entities import ProviderDeployment
from app.utils import Clock


@dataclass
class BreakerState:
    consecutive_failures: int = 0
    opened_at: float | None = None
    results: deque = field(default_factory=lambda: deque(maxlen=200))  # (ts, ok)


class CircuitBreaker:
    """Per-deployment breaker: opens on 5 consecutive failures or >50% error
    rate over 1 minute; half-open probe after 30s (AIG-FR-009)."""

    def __init__(self, settings: Settings, clock: Clock):
        self.settings = settings
        self.clock = clock
        self._states: dict[str, BreakerState] = {}

    def _state(self, deployment_id: str) -> BreakerState:
        return self._states.setdefault(deployment_id, BreakerState())

    def _ts(self) -> float:
        return self.clock.now().timestamp()

    def record(self, deployment_id: str, ok: bool) -> None:
        st = self._state(deployment_id)
        now = self._ts()
        st.results.append((now, ok))
        if ok:
            st.consecutive_failures = 0
            st.opened_at = None
            return
        st.consecutive_failures += 1
        window = [r for t, r in st.results if now - t <= self.settings.breaker_window_seconds]
        error_rate = (window.count(False) / len(window)) if window else 0.0
        if (
            st.consecutive_failures >= self.settings.breaker_consecutive_failures
            or (len(window) >= 4 and error_rate > self.settings.breaker_error_rate_threshold)
        ):
            st.opened_at = now

    def allows(self, deployment_id: str) -> bool:
        st = self._states.get(deployment_id)
        if st is None or st.opened_at is None:
            return True
        if self._ts() - st.opened_at >= self.settings.breaker_halfopen_after_seconds:
            return True  # half-open: allow a probe request through
        return False

    def state_of(self, deployment_id: str) -> str:
        st = self._states.get(deployment_id)
        if st is None or st.opened_at is None:
            return "closed"
        if self._ts() - st.opened_at >= self.settings.breaker_halfopen_after_seconds:
            return "half_open"
        return "open"


class HealthRegistry:
    """Active-probe health (AIG-FR-009a): unhealthy deployments are skipped in
    routing (like draining) without changing persisted status."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._failures: dict[str, int] = {}

    def record_probe(self, deployment_id: str, ok: bool) -> None:
        if ok:
            self._failures.pop(deployment_id, None)
        else:
            self._failures[deployment_id] = self._failures.get(deployment_id, 0) + 1

    def healthy(self, deployment_id: str) -> bool:
        return self._failures.get(deployment_id, 0) < self.settings.probe_failure_threshold


@dataclass
class Candidates:
    deployments: list[ProviderDeployment]
    cross_cloud: bool
    evaluation_order: list[str]  # deployment ids in evaluated order (AIG-FR-009b)


class Router:
    def __init__(self, settings: Settings, breaker: CircuitBreaker, health: HealthRegistry):
        self.settings = settings
        self.breaker = breaker
        self.health = health

    def candidates(self, deployments: list[ProviderDeployment], model_alias: str,
                   cell_cloud: str | None) -> Candidates:
        """Same-cloud first; cross-cloud only if no same-cloud deployment
        serves the rung (AIG-FR-004). Lower priority number = preferred."""
        pool = [
            d for d in deployments
            if d.model_family == model_alias
            and d.status == "active"
            and d.deleted_at is None
            and self.health.healthy(d.id)
            and self.breaker.allows(d.id)
        ]
        pool.sort(key=lambda d: d.priority)
        same = [d for d in pool if cell_cloud and d.cloud == cell_cloud]
        if same:
            return Candidates(same, cross_cloud=False,
                              evaluation_order=[d.id for d in pool])
        return Candidates(pool, cross_cloud=bool(pool) and cell_cloud is not None,
                          evaluation_order=[d.id for d in pool])


def backoff_ms(settings: Settings, rng: random.Random | None = None) -> int:
    """Jittered backoff 250ms–1s (AIG-FR-008)."""
    r = rng or random
    return r.randint(settings.retry_backoff_min_ms, settings.retry_backoff_max_ms)


class AttemptPlan:
    """Failover attempt sequence: retry once on the same deployment, then the
    next deployment; ≤3 total attempts spanning ≤2 distinct providers."""

    def __init__(self, candidates: list[ProviderDeployment], max_attempts: int = 3,
                 max_providers: int = 2):
        self.sequence: list[ProviderDeployment] = []
        providers: list[str] = []
        for d in candidates:
            if d.provider not in providers:
                if len(providers) >= max_providers:
                    continue
                providers.append(d.provider)
            self.sequence.append(d)  # first try
            self.sequence.append(d)  # one retry on same deployment
            if len(self.sequence) >= max_attempts:
                break
        self.sequence = self.sequence[:max_attempts]
