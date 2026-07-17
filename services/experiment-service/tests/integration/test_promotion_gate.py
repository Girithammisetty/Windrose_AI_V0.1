"""Integration (real Postgres): the governed promotion gate under RLS — register
none -> staging -> production with the approval gate, four-eyes enforcement, and
the single-production invariant (EXP-FR-031..033, AC-6/7/8)."""

from __future__ import annotations

import uuid

import pytest

from app.domain.errors import Conflict, RunNotFinished, SelfApprovalForbidden
from tests.conftest import TENANT_A, ctx_for, make_experiment

pytestmark = pytest.mark.integration


async def _finished_run(container, ctx, exp_id, mlflow_run_id, f1):
    await container.run_service.create_from_pipeline(
        ctx, {"mlflow_run_id": mlflow_run_id, "experiment_id": exp_id, "algorithm": "xgboost",
              "input_dataset_urns": [f"wr:{ctx.tenant_id}:dataset:dataset/d1"]})
    await container.run_service.transition_status(ctx, "pipeline.run.started",
                                                  {"mlflow_run_id": mlflow_run_id})
    run = await container.run_service.transition_status(
        ctx, "pipeline.run.succeeded", {"mlflow_run_id": mlflow_run_id})
    await container.mirror_service._apply_run_data(ctx, {
        "run_id": mlflow_run_id,
        "data": {"metrics": [{"key": "f1_score", "value": f1, "step": 0,
                              "timestamp": 1_700_000_000_000}]}})
    return run


async def test_full_promotion_gate_and_single_production(container):
    ctx = ctx_for(TENANT_A, sub="requester")
    exp = await make_experiment(container, ctx, name=f"gate-{uuid.uuid4().hex[:8]}")
    r1 = await _finished_run(container, ctx, exp.id, f"r1-{uuid.uuid4().hex[:6]}", 0.80)
    r2 = await _finished_run(container, ctx, exp.id, f"r2-{uuid.uuid4().hex[:6]}", 0.92)
    mname = f"model-{uuid.uuid4().hex[:8]}"
    reg1 = await container.registry_service.register(ctx, exp.id, r1.id, {"model_name": mname})
    await container.registry_service.register(ctx, exp.id, r2.id, {"model_name": mname})
    mid = reg1["model_id"]
    approver = ctx_for(TENANT_A, sub="reviewer")

    async def approve(version, target):
        promo = await container.promotion_service.promote(
            ctx, mid, version, {"target_stage": target})
        return await container.promotion_service.decide(approver, promo["promotion_id"], "approve")

    # v1: none -> staging -> production (gate enforced, human approval)
    await approve(1, "staging")
    await approve(1, "production")
    assert (await container.registry_service.get_version(ctx, mid, 1))["stage"] == "production"

    # v2 -> production auto-archives v1 (single-production invariant, AC-7)
    await approve(2, "staging")
    await approve(2, "production")
    assert (await container.registry_service.get_version(ctx, mid, 2))["stage"] == "production"
    assert (await container.registry_service.get_version(ctx, mid, 1))["stage"] == "archived"


async def test_four_eyes_and_pending_conflict(container):
    ctx = ctx_for(TENANT_A, sub="alice")
    exp = await make_experiment(container, ctx, name=f"fe-{uuid.uuid4().hex[:8]}")
    run = await _finished_run(container, ctx, exp.id, f"r-{uuid.uuid4().hex[:6]}", 0.9)
    mname = f"m-{uuid.uuid4().hex[:8]}"
    reg = await container.registry_service.register(ctx, exp.id, run.id, {"model_name": mname})
    promo = await container.promotion_service.promote(ctx, reg["model_id"], 1,
                                                      {"target_stage": "staging"})
    # requester cannot approve their own promotion (BR-6)
    with pytest.raises(SelfApprovalForbidden):
        await container.promotion_service.decide(ctx, promo["promotion_id"], "approve")
    # only one pending promotion per version (BR-4 / DB partial-unique index)
    with pytest.raises(Conflict):
        await container.promotion_service.promote(ctx, reg["model_id"], 1,
                                                  {"target_stage": "staging"})


async def test_register_requires_finished_run(container):
    ctx = ctx_for(TENANT_A)
    exp = await make_experiment(container, ctx, name=f"nf-{uuid.uuid4().hex[:8]}")
    mlf = f"r-{uuid.uuid4().hex[:6]}"
    await container.run_service.create_from_pipeline(
        ctx, {"mlflow_run_id": mlf, "experiment_id": exp.id})
    run = await container.run_service.transition_status(
        ctx, "pipeline.run.failed", {"mlflow_run_id": mlf})
    with pytest.raises(RunNotFinished):
        await container.registry_service.register(ctx, exp.id, run.id, {"model_name": "x"})
