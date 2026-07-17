"""Suite + scorer PATCH updates: editable-field mutation persists, identity is
immutable, and updates are tenant-scoped (cross-tenant -> NotFound)."""

from __future__ import annotations

import pytest

from app.domain.entities import CallCtx
from app.domain.errors import NotFound

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"

GATE = "sql_result_equivalence.mean >= baseline - 0.02"


def ctx(tenant=TENANT_A):
    return CallCtx(tenant_id=tenant, actor={"type": "user", "id": "curator-1"})


def _suite_body():
    return {
        "suite_id": "analytics-gate",
        "agent_key": "analytics",
        "datasets": [{"dataset_key": "analytics/nl2sql", "version": 1}],
        "scorers": [{"scorer": "sql_result_equivalence", "version": 2, "weight": 1.0}],
        "gate_rule": GATE,
        "min_cases": 1,
    }


def _scorer_body():
    return {
        "scorer_key": "my_custom_scorer",
        "version": 1,
        "kind": "deterministic",
        "gate_eligible": True,
        "config_schema": {"threshold": 0.5},
        "status": "draft",
    }


# ---- suites ----


async def test_suite_update_persists_editable_fields(container):
    svc = container.suite_service
    await svc.create(ctx(), _suite_body())
    updated = await svc.update(
        ctx(),
        "analytics-gate",
        {"min_cases": 5, "baseline_version": "v7"},
    )
    assert updated.min_cases == 5 and updated.baseline_version == "v7"
    # identity is untouched
    assert updated.suite_id == "analytics-gate" and updated.agent_key == "analytics"
    reloaded = await svc.get(ctx(), "analytics-gate")
    assert reloaded.min_cases == 5 and reloaded.baseline_version == "v7"


async def test_suite_update_rejects_judge_only_gate_rule(container):
    svc = container.suite_service
    await svc.create(ctx(), _suite_body())
    from app.domain.errors import JudgeGatesAlone

    with pytest.raises(JudgeGatesAlone):
        await svc.update(ctx(), "analytics-gate", {"gate_rule": "groundedness.mean >= 3.0"})


async def test_suite_update_cross_tenant_not_found(container):
    svc = container.suite_service
    await svc.create(ctx(TENANT_A), _suite_body())
    with pytest.raises(NotFound):
        await svc.update(ctx(TENANT_B), "analytics-gate", {"min_cases": 9})


# ---- scorers ----


async def test_scorer_update_persists_editable_fields(container):
    svc = container.scorer_service
    await svc.register(ctx(), _scorer_body())
    updated = await svc.update(
        ctx(),
        "my_custom_scorer",
        {"status": "active", "config_schema": {"threshold": 0.9}},
    )
    assert updated.status == "active" and updated.config_schema == {"threshold": 0.9}
    # identity is untouched
    assert updated.scorer_key == "my_custom_scorer" and updated.version == 1
    assert updated.kind == "deterministic"


async def test_scorer_update_judge_never_gate_eligible(container):
    svc = container.scorer_service
    await svc.register(
        ctx(),
        {"scorer_key": "my_judge", "version": 1, "kind": "llm_judge", "status": "draft"},
    )
    updated = await svc.update(ctx(), "my_judge", {"gate_eligible": True})
    # BR-1: a judge scorer can never gate, even if the patch asks for it.
    assert updated.gate_eligible is False


async def test_scorer_update_cross_tenant_not_found(container):
    svc = container.scorer_service
    await svc.register(ctx(TENANT_A), _scorer_body())
    with pytest.raises(NotFound):
        await svc.update(ctx(TENANT_B), "my_custom_scorer", {"status": "active"})
