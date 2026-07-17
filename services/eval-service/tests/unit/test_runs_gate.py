"""Eval runs + CI gate vs baseline (EVL-FR-020..032, AC-1/2/7/12/13)."""

from __future__ import annotations

import pytest

from app.container import build_container
from app.domain.entities import CallCtx
from app.domain.errors import BaselineIncomparable, EvalBudgetExceeded
from tests.conftest import FakeJudgeClient, make_settings

TENANT = "11111111-1111-4111-8111-111111111111"


def ctx():
    return CallCtx(tenant_id=TENANT, actor={"type": "user", "id": "eng-1"})


async def _setup(container, sqls):
    """Seed a fixture warehouse, an active analytics dataset with sql cases, and a
    gate suite (sql_result_equivalence + groundedness). Returns (case_ids, suite)."""
    container.warehouse.seed(
        "fw-test",
        {"orders": (["region", "net_revenue"], [("EMEA", 100.0), ("AMER", 200.0), ("EMEA", 50.0)])},
    )
    cs = container.case_service
    case_ids = []
    for i, sql in enumerate(sqls):
        c = await cs.create(
            ctx(),
            {
                "dataset_key": "analytics/nl2sql",
                "agent_key": "analytics",
                "input": {
                    "messages": [{"role": "user", "content": f"q{i}"}],
                    "context_refs": {"fixture_warehouse": "fw-test"},
                },
                "expected": {
                    "kind": "sql_result",
                    "value": {"sql": sql, "order_insensitive": True},
                },
                "source": "manual",
                "status": "active",
            },
        )
        case_ids.append(c.id)
    suite = await container.suite_service.create(
        ctx(),
        {
            "suite_id": "analytics-gate",
            "agent_key": "analytics",
            "datasets": [{"dataset_key": "analytics/nl2sql", "version": 1}],
            "scorers": [
                {
                    "scorer": "sql_result_equivalence",
                    "version": 2,
                    "weight": 0.7,
                    "regression_threshold": -0.02,
                },
                {
                    "scorer": "groundedness",
                    "version": 3,
                    "weight": 0.3,
                    "config": {"pass_threshold": 3.0},
                },
            ],
            "gate_rule": "sql_result_equivalence.mean >= baseline - 0.02 "
            "AND groundedness.mean >= baseline - 0.3",
            "min_cases": 1,
        },
    )
    return case_ids, suite


async def test_full_run_and_gate_pass_vs_baseline(container):  # AC-1
    good_sql = "SELECT region, SUM(net_revenue) AS rev FROM orders GROUP BY region"
    case_ids, _ = await _setup(container, [good_sql, good_sql])
    outputs = {
        cid: {"sql": good_sql, "answer": "revenue by region", "evidence": ["orders"]}
        for cid in case_ids
    }
    run = await container.run_service.create_and_execute(
        ctx(),
        trigger="ci",
        agent_key="analytics",
        candidate={"content_digest": "sha256:cand1"},
        suite_id="analytics-gate",
        candidate_provider=container.candidate_provider(outputs),
        baseline={
            "dataset_version": 1,
            "aggregates": {"sql_result_equivalence": {"mean": 1.0}, "groundedness": {"mean": 4.0}},
        },
    )
    assert run.status == "completed"
    aggs = run.totals["aggregates"]
    assert aggs["sql_result_equivalence"]["mean"] == 1.0
    gate = await container.gate_service.evaluate_from_run(ctx(), run.id)
    assert gate.gate_passed is True
    verdict_scorers = [v["scorer"] for v in gate.verdicts]
    assert any(s.startswith("sql_result_equivalence") for s in verdict_scorers)


async def test_gate_fails_on_regression_with_diff_and_event(container):  # AC-2
    good_sql = "SELECT region, SUM(net_revenue) AS rev FROM orders GROUP BY region"
    bad_sql = (
        "SELECT region, SUM(net_revenue) AS rev FROM orders WHERE region='AMER' GROUP BY region"
    )
    case_ids, _ = await _setup(container, [good_sql, good_sql])
    # one candidate wrong -> mean 0.5 vs baseline 1.0 (drop 0.5 > 0.02)
    outputs = {
        case_ids[0]: {"sql": good_sql, "answer": "x", "evidence": []},
        case_ids[1]: {"sql": bad_sql, "answer": "x", "evidence": []},
    }
    run = await container.run_service.create_and_execute(
        ctx(),
        trigger="ci",
        agent_key="analytics",
        candidate={"content_digest": "sha256:cand2"},
        suite_id="analytics-gate",
        candidate_provider=container.candidate_provider(outputs),
        baseline={
            "dataset_version": 1,
            "aggregates": {"sql_result_equivalence": {"mean": 1.0}, "groundedness": {"mean": 4.0}},
        },
    )
    gate = await container.gate_service.evaluate_from_run(ctx(), run.id)
    assert gate.gate_passed is False
    failing = [v for v in gate.verdicts if not v["passed"]]
    assert any("sql_result_equivalence" in v["scorer"] for v in failing)
    assert gate.failed_cases_sample and "diff" in gate.failed_cases_sample[0]["details"]
    # gate.completed event emitted to the outbox with gate_passed False
    events = [
        o["payload"]
        for o in container.memory_state.outbox
        if o["payload"]["event_type"] == "gate.completed"
    ]
    assert events and events[-1]["payload"]["gate_passed"] is False


async def test_baseline_incomparable_on_dataset_version_mismatch(container):  # AC-7
    good_sql = "SELECT region, SUM(net_revenue) AS rev FROM orders GROUP BY region"
    case_ids, _ = await _setup(container, [good_sql])
    outputs = {case_ids[0]: {"sql": good_sql, "answer": "x", "evidence": []}}
    run = await container.run_service.create_and_execute(
        ctx(),
        trigger="ci",
        agent_key="analytics",
        candidate={"content_digest": "sha256:cand3"},
        suite_id="analytics-gate",
        candidate_provider=container.candidate_provider(outputs),
        baseline={
            "dataset_version": 99,
            "aggregates": {"sql_result_equivalence": {"mean": 1.0}, "groundedness": {"mean": 4.0}},
        },
    )
    with pytest.raises(BaselineIncomparable):
        await container.gate_service.evaluate_from_run(ctx(), run.id)


async def test_baseline_missing_for_relative_rule_fails_safe(container):  # BR-2
    good_sql = "SELECT region, SUM(net_revenue) AS rev FROM orders GROUP BY region"
    case_ids, _ = await _setup(container, [good_sql])
    outputs = {case_ids[0]: {"sql": good_sql, "answer": "x", "evidence": []}}
    run = await container.run_service.create_and_execute(
        ctx(),
        trigger="ci",
        agent_key="analytics",
        candidate={"content_digest": "sha256:cand4"},
        suite_id="analytics-gate",
        candidate_provider=container.candidate_provider(outputs),
        baseline=None,
    )
    with pytest.raises(BaselineIncomparable):
        await container.gate_service.evaluate_from_run(ctx(), run.id)


async def test_reproducible_deterministic_scores(container):  # AC-12
    good_sql = "SELECT region, SUM(net_revenue) AS rev FROM orders GROUP BY region"
    case_ids, _ = await _setup(container, [good_sql, good_sql])
    outputs = {cid: {"sql": good_sql, "answer": "x", "evidence": []} for cid in case_ids}
    r1 = await container.run_service.create_and_execute(
        ctx(),
        trigger="manual",
        agent_key="analytics",
        candidate={"content_digest": "sha256:d"},
        suite_id="analytics-gate",
        candidate_provider=container.candidate_provider(outputs),
    )
    r2 = await container.run_service.create_and_execute(
        ctx(),
        trigger="manual",
        agent_key="analytics",
        candidate={"content_digest": "sha256:d"},
        suite_id="analytics-gate",
        candidate_provider=container.candidate_provider(outputs),
    )
    assert (
        r1.totals["aggregates"]["sql_result_equivalence"]
        == r2.totals["aggregates"]["sql_result_equivalence"]
    )
    # run record proves the pins
    assert r1.suite_pins["suite_id"] == "analytics-gate"
    assert r1.candidate["content_digest"] == "sha256:d"


async def test_budget_exceeded_retains_partial(tmp_path):  # AC-13
    # costly judge forces the per-run cap to blow after the first case.
    costly = FakeJudgeClient(rating=4.0, cost_usd=10.0)
    container = build_container(make_settings(tmp_path), mode="memory", judge_client=costly)
    good_sql = "SELECT region, SUM(net_revenue) AS rev FROM orders GROUP BY region"
    case_ids, _ = await _setup(container, [good_sql, good_sql, good_sql])
    outputs = {cid: {"sql": good_sql, "answer": "x", "evidence": []} for cid in case_ids}
    with pytest.raises(EvalBudgetExceeded):
        await container.run_service.create_and_execute(
            ctx(),
            trigger="ci",
            agent_key="analytics",
            candidate={"content_digest": "sha256:bust"},
            suite_id="analytics-gate",
            candidate_provider=container.candidate_provider(outputs),
            cost_cap_usd=5.0,
        )
    # partial results retained + run marked failed
    page = await container.run_service.list(ctx(), agent_key="analytics")
    failed = [r for r in page.items if r.status == "failed"]
    assert failed
    results = await container.run_service.list_cases(ctx(), failed[0].id)
    assert len(results) >= 1  # partial results kept
