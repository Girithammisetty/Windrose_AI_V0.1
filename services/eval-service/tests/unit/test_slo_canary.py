"""SLO computation, canary comparison, judge activation (EVL-FR-040/051/014)."""

from __future__ import annotations

import pytest

from app.domain.canary import compare
from app.domain.entities import CallCtx
from app.domain.errors import JudgeAgreementTooLow
from app.domain.slo import compute_metrics, empty_counters, fold_agent_run, fold_token_usage

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"


def ctx(t=TENANT_A):
    return CallCtx(tenant_id=t, actor={"type": "user", "id": "op-1"})


def test_slo_formulas():  # normative formulas (BRD §4)
    c = empty_counters()
    for _ in range(8):
        fold_agent_run(c, {"outcome": "completed", "full_answer_ms": 1000})
    fold_agent_run(c, {"outcome": "failed"})
    fold_agent_run(c, {"outcome": "human_handoff"})
    for _ in range(10):
        fold_token_usage(c, {"cost_usd": 0.02})
    m = compute_metrics(c)
    # completion denom = completed+failed+expired+abandoned (handoff excluded)
    assert round(m["task_completion_rate"], 3) == round(8 / 9, 3)
    assert round(m["escalation_rate"], 3) == round(1 / 10, 3)  # handoff / total_runs
    assert round(m["cost_per_completed_task"], 4) == round(0.2 / 8, 4)


async def test_slo_streaming_and_tenant_isolation(container):  # AC-10 / AC-14
    slo = container.slo_service
    for _ in range(6):
        await slo.ingest_event(
            TENANT_A,
            "case-triage",
            "v3",
            "agent_run",
            {"outcome": "completed", "full_answer_ms": 900},
        )
    await slo.ingest_event(TENANT_A, "case-triage", "v3", "agent_run", {"outcome": "failed"})
    await slo.ingest_event(TENANT_A, "case-triage", "v3", "token_usage", {"cost_usd": 0.5})
    await slo.ingest_event(TENANT_B, "case-triage", "v3", "agent_run", {"outcome": "completed"})

    # tenant admin (own slice): only tenant A rows, never the platform rollup
    admin_view = await slo.query(ctx(TENANT_A), "case-triage", window="24h", operator=False)
    assert admin_view
    assert all(r["tenant_id"] == TENANT_A for r in admin_view)
    a_metrics = admin_view[0]["metrics"]
    assert round(a_metrics["task_completion_rate"], 3) == round(6 / 7, 3)

    # operator: platform (tenant_id None) cross-tenant rollup available
    op_view = await slo.query(ctx(TENANT_A), "case-triage", window="24h", operator=True)
    platform = [r for r in op_view if r["tenant_id"] is None]
    assert platform and platform[0]["metrics"]["sample_n"] >= 8  # A(7) + B(1)


async def test_slo_budget_burn_alert(container):  # AC-10 burn
    slo = container.slo_service
    await slo.set_targets(ctx(), "analytics", "v1", {"task_completion_rate": {"min": 0.9}})
    # 2 completed, 3 failed -> completion 0.4 < 0.9 target -> burn
    for _ in range(2):
        await slo.ingest_event(TENANT_A, "analytics", "v1", "agent_run", {"outcome": "completed"})
    alerts = []
    for _ in range(3):
        alerts = await slo.ingest_event(
            TENANT_A, "analytics", "v1", "agent_run", {"outcome": "failed"}
        )
    assert any(a["metric"] == "task_completion_rate" for a in alerts)


def test_canary_compare_and_early_stop():  # AC-8
    # candidate slightly better, no regression -> promote
    good = {"tool_selection_accuracy": [(0.94, 0.91)] * 200}
    rep = compare(good, {"tool_selection_accuracy": -0.05}, {"tool_selection_accuracy"})
    assert rep["recommendation"] == "promote" and not rep["any_regressed"]
    assert rep["metrics"][0]["ci95"]

    # Must scorer regresses 2x threshold at >=50 samples -> early stop
    bad = {"tool_selection_accuracy": [(0.70, 0.91)] * 60}
    rep2 = compare(bad, {"tool_selection_accuracy": -0.05}, {"tool_selection_accuracy"})
    assert rep2["early_stop"] is not None
    assert rep2["early_stop"]["scorer"] == "tool_selection_accuracy"


async def test_canary_service_flow(container):  # AC-8 event
    c = await container.canary_service.create(
        ctx(),
        {
            "agent_key": "analytics",
            "candidate_version": "v15",
            "baseline_version": "v14",
            "mode": "paired_shadow",
            "sample_spec": {"min_samples": 200},
            "thresholds": {"groundedness": -0.3},
            "must_scorers": ["groundedness"],
        },
    )
    updated = await container.canary_service.ingest_samples(
        ctx(), c.comparison_id, {"groundedness": [[4.15, 4.22]] * 200}
    )
    assert updated.status == "ready"
    events = [
        o["payload"]
        for o in container.memory_state.outbox
        if o["payload"]["event_type"] == "canary.scored"
    ]
    assert events


async def test_judge_activation_agreement_gate(container):  # AC-11
    sc = container.scorer_service
    await sc.register(
        ctx(),
        {
            "scorer_key": "groundedness",
            "version": 4,
            "kind": "llm_judge",
            "judge_agreement": 0.72,
            "status": "draft",
        },
    )
    with pytest.raises(JudgeAgreementTooLow):
        await sc.activate(ctx(), "groundedness", 4)
    await sc.register(
        ctx(),
        {
            "scorer_key": "groundedness",
            "version": 5,
            "kind": "llm_judge",
            "judge_agreement": 0.85,
            "status": "draft",
        },
    )
    activated = await sc.activate(ctx(), "groundedness", 5)
    assert activated.status == "active"
