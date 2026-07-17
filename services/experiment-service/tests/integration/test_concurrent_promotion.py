"""FINDING-3 / BR-4: concurrent production approvals are serialized by the
per-model mutex — the single-production invariant holds and no approval 500s
(the loser, if any, gets a clean 409, never an IntegrityError)."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.domain.errors import Conflict
from tests.conftest import TENANT_A, ctx_for, make_experiment

pytestmark = pytest.mark.integration


async def _finished_run(container, ctx, exp_id, mlflow_run_id, f1):
    await container.run_service.create_from_pipeline(
        ctx, {"mlflow_run_id": mlflow_run_id, "experiment_id": exp_id, "algorithm": "xgboost"})
    await container.run_service.transition_status(ctx, "pipeline.run.started",
                                                  {"mlflow_run_id": mlflow_run_id})
    run = await container.run_service.transition_status(
        ctx, "pipeline.run.succeeded", {"mlflow_run_id": mlflow_run_id})
    await container.mirror_service._apply_run_data(ctx, {
        "run_id": mlflow_run_id,
        "data": {"metrics": [{"key": "f1_score", "value": f1, "step": 0,
                              "timestamp": 1_700_000_000_000}]}})
    return run


async def test_concurrent_production_approvals_serialize(container):
    ctx = ctx_for(TENANT_A, sub="requester")
    approver = ctx_for(TENANT_A, sub="reviewer")
    exp = await make_experiment(container, ctx, name=f"cc-{uuid.uuid4().hex[:8]}")
    r1 = await _finished_run(container, ctx, exp.id, f"c1-{uuid.uuid4().hex[:6]}", 0.80)
    r2 = await _finished_run(container, ctx, exp.id, f"c2-{uuid.uuid4().hex[:6]}", 0.90)
    mname = f"cm-{uuid.uuid4().hex[:8]}"
    reg = await container.registry_service.register(ctx, exp.id, r1.id, {"model_name": mname})
    await container.registry_service.register(ctx, exp.id, r2.id, {"model_name": mname})
    mid = reg["model_id"]

    # both versions -> staging (approved, sequential)
    for ver in (1, 2):
        p = await container.promotion_service.promote(ctx, mid, ver, {"target_stage": "staging"})
        await container.promotion_service.decide(approver, p["promotion_id"], "approve")

    # two pending production promotions
    p1 = await container.promotion_service.promote(ctx, mid, 1, {"target_stage": "production"})
    p2 = await container.promotion_service.promote(ctx, mid, 2, {"target_stage": "production"})

    # approve BOTH concurrently
    results = await asyncio.gather(
        container.promotion_service.decide(approver, p1["promotion_id"], "approve"),
        container.promotion_service.decide(approver, p2["promotion_id"], "approve"),
        return_exceptions=True)

    # No unhandled/500 error: any failure must be a clean domain Conflict (409).
    for r in results:
        assert not isinstance(r, Exception) or isinstance(r, Conflict), r
    approved = [r for r in results if isinstance(r, dict) and r.get("status") == "approved"]
    assert len(approved) >= 1

    # single-production invariant holds: exactly one production version.
    v1 = await container.registry_service.get_version(ctx, mid, 1)
    v2 = await container.registry_service.get_version(ctx, mid, 2)
    prod = [v for v in (v1, v2) if v["stage"] == "production"]
    assert len(prod) == 1
