"""EXP-FR-003/004/005/011/012: run creation from pipeline events, status
transitions, webhook mirror ingest + idempotency, hidden-param filtering."""

from __future__ import annotations

import pytest

from app.domain.entities import RUN_STATUS
from app.domain.errors import NotFound
from tests.conftest import ctx_for, make_experiment, uid


async def test_run_created_from_pipeline_submitted_ac1(container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx)
    run = await container.run_service.create_from_pipeline(
        ctx, {"mlflow_run_id": "mlf-1", "experiment_id": exp.id})
    assert run.status == RUN_STATUS["scheduled"]
    # run.mirrored emitted
    assert container.bus.events_of_type("run.mirrored")


async def test_status_transitions_from_pipeline_events(container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx)
    await container.run_service.create_from_pipeline(
        ctx, {"mlflow_run_id": "mlf-2", "experiment_id": exp.id})
    await container.run_service.transition_status(ctx, "pipeline.run.started",
                                                  {"mlflow_run_id": "mlf-2"})
    run = await container.run_service.transition_status(
        ctx, "pipeline.run.succeeded", {"mlflow_run_id": "mlf-2"})
    assert run.status == RUN_STATUS["finished"]
    # terminal states never move backward
    run2 = await container.run_service.transition_status(
        ctx, "pipeline.run.started", {"mlflow_run_id": "mlf-2"})
    assert run2.status == RUN_STATUS["finished"]


async def test_webhook_metric_reflected_and_hidden_filtered(container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx)
    run = await container.run_service.create_from_pipeline(
        ctx, {"mlflow_run_id": "mlf-3", "experiment_id": exp.id})
    changed = await container.mirror_service._apply_run_data(ctx, {
        "run_id": "mlf-3",
        "data": {"metrics": [{"key": "f1_score", "value": 0.91, "step": 10,
                              "timestamp": 1_700_000_000_000}],
                 "params": [{"key": "max_depth", "value": "6"},
                            {"key": "n_workers", "value": "8"}]}})  # n_workers hidden
    assert changed
    detail = await container.run_service.get_detail(ctx, run.id)
    assert detail["metrics"]["f1_score"]["value"] == 0.91
    assert "max_depth" in detail["params"]
    assert "n_workers" not in detail["params"]  # hidden by default (BR-11)
    detail_all = await container.run_service.get_detail(ctx, run.id, include_hidden=True)
    assert "n_workers" in detail_all["params"]


async def test_webhook_delivery_idempotent_ac4(container):
    ctx = ctx_for()
    d = uid()
    first = await container.mirror_service.ingest_webhook(
        tenant_id=ctx.tenant_id, delivery_id=d, event_type="run.updated",
        payload={"run_id": "x", "data": {}})
    second = await container.mirror_service.ingest_webhook(
        tenant_id=ctx.tenant_id, delivery_id=d, event_type="run.updated",
        payload={"run_id": "x", "data": {}})
    assert first is True and second is False  # dedup on delivery id


async def test_param_write_once_conflict_flagged(container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx)
    await container.run_service.create_from_pipeline(
        ctx, {"mlflow_run_id": "mlf-4", "experiment_id": exp.id})
    await container.mirror_service._apply_run_data(
        ctx, {"run_id": "mlf-4", "data": {"params": [{"key": "seed", "value": "1"}]}})
    changed = await container.mirror_service._apply_run_data(
        ctx, {"run_id": "mlf-4", "data": {"params": [{"key": "seed", "value": "2"}]}})
    assert changed  # a changed logged param flags param_conflict (not overwritten)


async def test_webhook_before_run_row_parks(container):
    ctx = ctx_for()
    with pytest.raises(NotFound):  # NotFound -> parked/retried (BR-2)
        await container.mirror_service._apply_run_data(
            ctx, {"run_id": "never", "data": {"metrics": []}})
