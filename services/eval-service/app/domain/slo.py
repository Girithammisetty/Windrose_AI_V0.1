"""Agent SLO computation (EVL-FR-051). Normative formulas from BRD §4.

SLOs roll up from streaming events (``ai.agent_run.v1``, ``ai.proposal.v1``,
``ai.tool_invoked.v1``, ``ai.token_usage.v1``). The consumer folds events into
per (agent_key × version × tenant × window) counters; derived metrics are
computed on read so aggregates stay exact under out-of-order delivery."""

from __future__ import annotations


def empty_counters() -> dict:
    return {
        "completed": 0,
        "failed": 0,
        "expired_proposal": 0,
        "abandoned": 0,
        "handoff": 0,
        "rejected_proposal_terminal": 0,
        "total_runs": 0,
        "tool_invocations": 0,
        "tool_errors": 0,
        "approved": 0,
        "edited_approved": 0,
        "decided_proposals": 0,
        "cost_usd_sum": 0.0,
        "first_token_ms": [],
        "full_answer_ms": [],
    }


def fold_agent_run(counters: dict, payload: dict) -> None:
    counters["total_runs"] += 1
    outcome = payload.get("outcome") or payload.get("status")
    if outcome == "completed":
        counters["completed"] += 1
    elif outcome == "failed":
        counters["failed"] += 1
    elif outcome in ("expired_proposal", "expired"):
        counters["expired_proposal"] += 1
    elif outcome == "abandoned":
        counters["abandoned"] += 1
    elif outcome in ("human_handoff", "handoff", "escalated"):
        counters["handoff"] += 1
    ft = payload.get("first_token_ms")
    fa = payload.get("full_answer_ms")
    if ft is not None:
        counters["first_token_ms"].append(float(ft))
    if fa is not None:
        counters["full_answer_ms"].append(float(fa))


def fold_proposal(counters: dict, payload: dict) -> None:
    decision = payload.get("decision") or payload.get("event")
    if decision in ("approved", "rejected", "edited_approved"):
        counters["decided_proposals"] += 1
    if decision == "approved":
        counters["approved"] += 1
    elif decision == "edited_approved":
        counters["edited_approved"] += 1
    elif decision == "rejected":
        if payload.get("terminal", True):
            counters["rejected_proposal_terminal"] += 1


def fold_tool(counters: dict, payload: dict) -> None:
    counters["tool_invocations"] += 1
    if payload.get("error") or payload.get("status") == "error":
        counters["tool_errors"] += 1


def fold_token_usage(counters: dict, payload: dict) -> None:
    counters["cost_usd_sum"] += float(payload.get("cost_usd", 0.0))


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
    return s[idx]


def _safe_div(n: float, d: float) -> float | None:
    return (n / d) if d else None


def compute_metrics(counters: dict) -> dict:
    completed = counters["completed"]
    denom_completion = (
        completed + counters["failed"] + counters["expired_proposal"] + counters["abandoned"]
    )
    escalations = counters["handoff"] + counters["rejected_proposal_terminal"]
    return {
        "task_completion_rate": _safe_div(completed, denom_completion),
        "escalation_rate": _safe_div(escalations, counters["total_runs"]),
        "tool_error_rate": _safe_div(counters["tool_errors"], counters["tool_invocations"]),
        "proposal_acceptance_rate": _safe_div(
            counters["approved"] + counters["edited_approved"], counters["decided_proposals"]
        ),
        "p95_first_token_ms": _p95(counters["first_token_ms"]),
        "p95_full_answer_ms": _p95(counters["full_answer_ms"]),
        "cost_per_completed_task": _safe_div(counters["cost_usd_sum"], completed),
        "sample_n": counters["total_runs"],
    }


def budget_burn(metrics: dict, targets: dict) -> list[dict]:
    """Return budget-burn alerts where a metric breaches its SLO target
    (EVL-FR-051). ``targets`` maps metric -> {min|max: value}."""
    alerts = []
    for metric, target in (targets or {}).items():
        value = metrics.get(metric)
        if value is None:
            continue
        if "min" in target and value < target["min"]:
            alerts.append(
                {
                    "metric": metric,
                    "value": value,
                    "target": target["min"],
                    "kind": "below_min",
                    "burn_rate": target["min"] - value,
                }
            )
        if "max" in target and value > target["max"]:
            alerts.append(
                {
                    "metric": metric,
                    "value": value,
                    "target": target["max"],
                    "kind": "above_max",
                    "burn_rate": value - target["max"],
                }
            )
    return alerts
