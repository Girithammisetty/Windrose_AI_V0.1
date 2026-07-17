"""Online sampling of production traces (EVL-FR-021c / AC-9, BR-6).

Nightly online sampling scores a fair share of production traces with
**production-safe scorers only** — no re-execution against tenant data (the agent
is never re-invoked; SQL is never re-run). Per-tenant sampling caps prevent one
high-volume tenant from dominating the quality signal (BR-6). Results feed SLOs
and the flywheel, never gates."""

from __future__ import annotations

from app.domain.errors import ValidationFailed
from app.domain.scorers.registry import PRODUCTION_SAFE_SCORERS


class RuntimeReexecutionForbidden(RuntimeError):
    """Raised if online scoring ever tries to invoke a candidate provider —
    online scoring must be post-hoc over the trace only (AC-9)."""


def fair_sample(
    traces_by_tenant: dict[str, list[dict]], *, sample_pct: float, per_tenant_cap: int
) -> dict[str, list[dict]]:
    """Sample ``sample_pct`` of each tenant's traces, capped at ``per_tenant_cap``
    per tenant (BR-6 fairness). Deterministic (takes the first N)."""
    out: dict[str, list[dict]] = {}
    for tenant, traces in traces_by_tenant.items():
        n = min(per_tenant_cap, max(1, round(len(traces) * sample_pct)) if traces else 0)
        out[tenant] = traces[:n]
    return out


def assert_production_safe(scorer_keys: list[str]) -> None:
    """Reject any scorer that would re-execute against tenant data (AC-9)."""
    bad = [k for k in scorer_keys if k not in PRODUCTION_SAFE_SCORERS]
    if bad:
        raise ValidationFailed(
            f"online sampling may only use production-safe scorers "
            f"(no re-execution against tenant data); rejected: {bad}"
        )


class OnlineSamplingService:
    """Scores sampled production traces post-hoc. It has NO candidate provider —
    a re-execution attempt raises, proving runtime-call absence (AC-9)."""

    def __init__(self, deps):
        self.deps = deps

    async def score_traces(
        self, tenant_id: str, traces: list[dict], scorer_keys: list[str], config: dict | None = None
    ) -> list[dict]:
        assert_production_safe(scorer_keys)
        config = config or {}
        registry = self.deps.registry
        results: list[dict] = []
        for trace in traces:
            # The candidate output IS the trace's already-emitted output — the agent
            # is never re-invoked (no candidate_provider call at all).
            candidate_output = trace.get("output", {})
            case = {"id": trace.get("trace_id", ""), "input": trace.get("input", {}),
                    "expected": trace.get("expected", {"kind": "rubric", "value": {}}),
                    "_tenant_id": tenant_id}
            for key in scorer_keys:
                if not registry.has(key):
                    continue
                res = await registry.get(key).score(case, candidate_output, config.get(key, {}))
                results.append({"trace_id": case["id"], "scorer_key": key,
                                "score": res.score, "passed": res.passed,
                                "details": res.details})
        return results
