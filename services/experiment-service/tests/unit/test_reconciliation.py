"""EXP-FR-013, AC-3: reconciliation sweep repairs missed changes then settles to 0."""

from __future__ import annotations

from tests.conftest import ctx_for, make_experiment


def _mlflow_run(run_id: str, experiment_id: str, f1: float) -> dict:
    return {
        "info": {"run_id": run_id, "experiment_id": experiment_id, "status": "FINISHED",
                 "start_time": 1_700_000_000_000, "end_time": 1_700_000_100_000,
                 "artifact_uri": f"s3://mlflow/{experiment_id}/{run_id}/artifacts"},
        "data": {"metrics": [{"key": "f1_score", "value": f1, "step": 0,
                              "timestamp": 1_700_000_000_000}],
                 "params": [{"key": "algorithm", "value": "xgboost"}], "tags": []},
    }


async def test_sweep_repairs_dropped_then_zero_ac3(container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx, name="recon")
    mlflow = container.deps.mlflow  # LocalMlflowClient
    # 5 runs exist in MLflow but their webhooks were "dropped" (not mirrored).
    for i in range(5):
        mlflow.seed_run(_mlflow_run(f"drop-{i}", exp.mlflow_experiment_id, 0.5 + i / 100))
    first = await container.reconciliation_service.sweep_tenant(ctx.tenant_id)
    assert first["repaired_count"] == 5
    # re-running the sweep with no new changes reports 0 drift.
    second = await container.reconciliation_service.sweep_tenant(ctx.tenant_id)
    assert second["repaired_count"] == 0
    assert container.bus.events_of_type("experiment.mirror.reconciled")
    # the mirror now serves the runs locally
    detail = await container.query_service.best_run(
        ctx, exp.id, "f1_score", "max", "finished")
    assert detail["metrics"]["f1_score"] == 0.54
