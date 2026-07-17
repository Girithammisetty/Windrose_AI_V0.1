"""Integration (REAL MLflow + Postgres mirror): register a run against the real
MLflow tracking server and prove experiment-service's mirror reflects it via the
reconciliation sweep — served locally with zero MLflow calls in the read path
(EXP-FR-013/014, AC-2/AC-3)."""

from __future__ import annotations

import time
import uuid

import pytest

from tests.conftest import (
    PIPE_FE,
    PIPE_MODEL,
    PIPE_TRAIN,
    TENANT_A,
    WORKSPACE,
    auth,
    ctx_for,
)
from tests.integration.conftest import mlflow_up

pytestmark = pytest.mark.integration


async def test_real_mlflow_run_mirrored_by_sweep(client, container, real_mlflow):
    if not mlflow_up():
        pytest.skip("MLflow not reachable")
    name = f"fraud-{uuid.uuid4().hex[:8]}"
    # 1) experiment creation is the only synchronous MLflow write (EXP-FR-001)
    resp = await client.post("/api/v1/experiments", json={
        "workspace_id": WORKSPACE, "name": name, "model_type": "classification",
        "model_pipeline_urn": PIPE_MODEL, "feature_engineering_pipeline_urn": PIPE_FE,
        "training_pipeline_urn": PIPE_TRAIN}, headers=auth(TENANT_A))
    assert resp.status_code == 201, resp.text
    exp = resp.json()["data"]
    mlflow_experiment_id = exp["mlflow_experiment_id"]

    # 2) a component logs a REAL run (params + metric) to MLflow
    now_ms = int(time.time() * 1000)
    run = await real_mlflow.create_run(mlflow_experiment_id, start_time=now_ms)
    run_id = run["info"]["run_id"]
    await real_mlflow.log_batch(
        run_id,
        metrics=[{"key": "f1_score", "value": 0.91, "timestamp": now_ms, "step": 0}],
        params=[{"key": "algorithm", "value": "xgboost"}])
    await real_mlflow.update_run(run_id, status="FINISHED", end_time=now_ms + 1000)

    # 3) the reconciliation sweep hits real MLflow REST and repairs the mirror
    result = await container.reconciliation_service.sweep_tenant(TENANT_A)
    assert result["repaired_count"] >= 1

    # 4) the mirror serves the run + metric from Postgres (no MLflow in read path)
    ctx = ctx_for(TENANT_A)
    best = await container.query_service.best_run(ctx, exp["id"], "f1_score", "max", "finished")
    assert best["mlflow_run_id"] == run_id
    assert best["metrics"]["f1_score"] == 0.91

    # 5) re-running the sweep reports zero drift (steady state, AC-3)
    second = await container.reconciliation_service.sweep_tenant(TENANT_A)
    assert second["repaired_count"] == 0
