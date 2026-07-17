"""BUG-2 (real MLflow registry): approving a promotion transitions the MLflow
model-version STAGE, so inference-service (which resolves models:/<name>/<stage>)
sees the governed decision. Promoting a new version to Production also archives
the incumbent's MLflow stage (EXP-FR-032)."""

from __future__ import annotations

import time
import uuid

import pytest

from tests.conftest import TENANT_A, ctx_for, make_experiment
from tests.integration.conftest import mlflow_up

pytestmark = pytest.mark.integration


async def _real_finished_run(real_mlflow, mlflow_experiment_id, f1):
    now = int(time.time() * 1000)
    run = await real_mlflow.create_run(mlflow_experiment_id, start_time=now)
    rid = run["info"]["run_id"]
    await real_mlflow.log_batch(
        rid, metrics=[{"key": "f1_score", "value": f1, "timestamp": now, "step": 0}],
        params=[{"key": "algorithm", "value": "xgboost"}])
    await real_mlflow.update_run(rid, status="FINISHED", end_time=now + 1000)
    return rid


def _mlflow_version(version_payload: dict) -> str:
    ref = version_payload["mlflow_model_ref"]
    assert ref.startswith("models:/"), f"expected a models:/ ref, got {ref!r}"
    return ref.rsplit("/", 1)[1]


async def test_approval_transitions_mlflow_stage_and_archives_incumbent(container, real_mlflow):
    if not mlflow_up():
        pytest.skip("MLflow not reachable")
    ctx = ctx_for(TENANT_A, sub="requester")
    approver = ctx_for(TENANT_A, sub="reviewer")
    exp = await make_experiment(container, ctx, name=f"mlst-{uuid.uuid4().hex[:8]}")

    rid1 = await _real_finished_run(real_mlflow, exp.mlflow_experiment_id, 0.80)
    rid2 = await _real_finished_run(real_mlflow, exp.mlflow_experiment_id, 0.92)
    await container.reconciliation_service.sweep_tenant(TENANT_A)

    async with container.deps.uow_factory(TENANT_A) as uow:
        r1 = await uow.runs.get_by_mlflow_run_id(rid1)
        r2 = await uow.runs.get_by_mlflow_run_id(rid2)

    mname = f"mlmodel-{uuid.uuid4().hex[:8]}"
    reg = await container.registry_service.register(ctx, exp.id, r1.id, {"model_name": mname})
    await container.registry_service.register(ctx, exp.id, r2.id, {"model_name": mname})
    mid = reg["model_id"]

    async def approve(version, target):
        p = await container.promotion_service.promote(ctx, mid, version, {"target_stage": target})
        await container.promotion_service.decide(approver, p["promotion_id"], "approve")

    # v1 -> Production
    await approve(1, "staging")
    await approve(1, "production")
    v1 = await container.registry_service.get_version(ctx, mid, 1)
    mlv1 = _mlflow_version(v1)
    mv1 = await real_mlflow.get_model_version(mname, mlv1)
    assert mv1["current_stage"] == "Production", mv1

    # v2 -> Production archives v1 in the MLflow registry too
    await approve(2, "staging")
    await approve(2, "production")
    v2 = await container.registry_service.get_version(ctx, mid, 2)
    mlv2 = _mlflow_version(v2)
    assert (await real_mlflow.get_model_version(mname, mlv2))["current_stage"] == "Production"
    assert (await real_mlflow.get_model_version(mname, mlv1))["current_stage"] == "Archived"
