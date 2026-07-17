"""Eval-run executor (EVL-FR-020). Fans out per-case executions (cap 20) against
a candidate-output provider, applies each suite scorer, enforces the per-run cost
cap (EVL-FR-023 / AC-13), and returns case results + per-scorer aggregates.

In production these fan-outs are Temporal workflow activities against
agent-runtime replay/no-side-effect mode; here the same scoring logic runs as an
in-process async engine driven by a candidate-output provider (real agent-runtime
replay client in real mode, or inline candidate outputs supplied by CI)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.adapters.candidate_provider import CandidateUnavailable
from app.domain.aggregate import aggregate_by_scorer
from app.domain.errors import EvalBudgetExceeded
from app.domain.scorers.registry import ScorerRegistry
from app.utils import new_id, utcnow

log = logging.getLogger("eval.runner")

FANOUT_CAP = 20


@dataclass
class RunOutcome:
    case_results: list[dict] = field(default_factory=list)
    aggregates: dict = field(default_factory=dict)
    totals: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    status: str = "completed"
    error: str | None = None
    # Honest degradation (EVL-FR-020): set when one or more cases had no REAL
    # candidate output (e.g. agent-runtime replay unimplemented / inline miss).
    # A degraded run is never reported as a clean pass — it fails with a loud,
    # named error rather than scoring empty candidates as if real.
    degraded: bool = False
    degraded_cases: int = 0


def _applies(scorer_key: str, expected_kind: str, registry: ScorerRegistry) -> bool:
    scorer = registry.get(scorer_key)
    kinds = getattr(scorer, "applicable_expected_kinds", ())
    return expected_kind in kinds


class EvalRunner:
    def __init__(self, registry: ScorerRegistry, candidate_provider, *, clock=None):
        self._registry = registry
        self._provider = candidate_provider
        self._clock = clock

    async def run(
        self,
        *,
        run_id: str,
        tenant_id: str,
        agent_key: str,
        candidate: dict,
        suite_scorers: list[dict],
        cases: list[dict],
        cost_cap_usd: float,
        memory_snapshot_ver: str | None = None,
    ) -> RunOutcome:
        outcome = RunOutcome()
        sem = asyncio.Semaphore(FANOUT_CAP)
        cost_lock = asyncio.Lock()
        state = {"cost": 0.0, "budget_hit": False, "degraded_cases": 0}

        async def score_case(case: dict) -> list[dict]:
            async with sem:
                return await self._score_one(
                    run_id,
                    tenant_id,
                    agent_key,
                    candidate,
                    suite_scorers,
                    case,
                    cost_cap_usd,
                    cost_lock,
                    state,
                    memory_snapshot_ver,
                )

        results_nested = await asyncio.gather(*(score_case(c) for c in cases))
        for rs in results_nested:
            outcome.case_results.extend(rs)

        outcome.cost_usd = state["cost"]
        outcome.degraded_cases = state["degraded_cases"]
        outcome.degraded = state["degraded_cases"] > 0
        outcome.aggregates = aggregate_by_scorer(outcome.case_results)
        outcome.totals = {
            "cases": len(cases),
            "case_results": len(outcome.case_results),
            "aggregates": outcome.aggregates,
            "cost_usd": round(state["cost"], 6),
            "degraded_cases": state["degraded_cases"],
        }
        if state["budget_hit"]:
            outcome.status = "failed"
            outcome.error = "EVAL_BUDGET_EXCEEDED"
        elif outcome.degraded:
            # No real candidate output for at least one case: fail the run with a
            # named cause rather than reporting a clean pass over empty candidates.
            outcome.status = "failed"
            outcome.error = "CANDIDATE_UNAVAILABLE"
        return outcome

    async def _score_one(
        self,
        run_id,
        tenant_id,
        agent_key,
        candidate,
        suite_scorers,
        case,
        cost_cap_usd,
        cost_lock,
        state,
        memory_snapshot_ver,
    ) -> list[dict]:
        if state["budget_hit"]:
            return []
        expected_kind = case.get("expected", {}).get("kind", "structured")
        case = {**case, "_tenant_id": tenant_id}
        try:
            candidate_output = await self._provider.candidate_output(
                agent_key=agent_key,
                candidate=candidate,
                case=case,
                memory_snapshot_ver=memory_snapshot_ver,
            )
        except CandidateUnavailable as exc:
            # No REAL candidate for this case. Log loudly and emit a single
            # degraded marker result — do NOT run scorers against an empty
            # candidate (which would masquerade as genuine pass/fail signal).
            log.error(
                "eval case degraded: no real candidate output (run_id=%s case_id=%s): %s",
                run_id,
                case.get("id"),
                exc.reason,
            )
            async with cost_lock:
                state["degraded_cases"] += 1
            return [
                {
                    "id": new_id(),
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "case_id": case["id"],
                    "scorer_key": "candidate_provider",
                    "scorer_version": 0,
                    "score": 0.0,
                    "passed": False,
                    "details": {"error": "candidate_unavailable", "detail": exc.reason[:400]},
                    "trace_ref": None,
                    "latency_ms": 0,
                    "cost_usd": 0.0,
                    "weight": case.get("weight", 1.0),
                    "created_at": utcnow(),
                }
            ]
        results: list[dict] = []
        for spec in suite_scorers:
            scorer_key = spec["scorer"]
            if not self._registry.has(scorer_key):
                continue
            if not _applies(scorer_key, expected_kind, self._registry):
                continue
            if state["budget_hit"]:
                break
            config = spec.get("config", {})
            t0 = time.monotonic()
            try:
                res = await self._registry.get(scorer_key).score(case, candidate_output, config)
            except Exception as exc:  # noqa: BLE001 - a scorer error fails that case, not the run
                res = _error_result(str(exc))
            latency_ms = (
                res.latency_ms
                if res.latency_ms is not None
                else int((time.monotonic() - t0) * 1000)
            )
            async with cost_lock:
                state["cost"] += res.cost_usd
                if state["cost"] > cost_cap_usd:
                    state["budget_hit"] = True
            results.append(
                {
                    "id": new_id(),
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "case_id": case["id"],
                    "scorer_key": scorer_key,
                    "scorer_version": spec.get("version", 1),
                    "score": res.score,
                    "passed": res.passed,
                    "details": res.details,
                    "trace_ref": res.trace_ref,
                    "latency_ms": latency_ms,
                    "cost_usd": res.cost_usd,
                    "weight": case.get("weight", 1.0),
                    "created_at": utcnow(),
                }
            )
        return results


def _error_result(msg: str) -> object:
    from app.domain.scorers.base import ScoreResult

    return ScoreResult(0.0, False, {"error": "scorer_error", "detail": msg[:400]})


def enforce_budget(outcome: RunOutcome) -> None:
    """Raise EvalBudgetExceeded (partial results already on the outcome)."""
    if outcome.error == "EVAL_BUDGET_EXCEEDED":
        raise EvalBudgetExceeded(
            f"eval run exceeded cost cap (spent ${outcome.cost_usd:.4f}); partial results retained"
        )
