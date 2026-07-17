"""Consumed-event handler tests (BRD §6, AC-12)."""

from __future__ import annotations

import uuid

import pytest

from app.domain.ports import CallCtx
from app.domain.services import SubmitRequest
from app.events.consumer import (
    DatasetEventHandler,
    PipelineEventHandler,
    UsageEventHandler,
)
from tests.conftest import TENANT_A, WORKSPACE, add_input_dataset

MODEL = f"wr:{TENANT_A}:experiment:model_version/fraud-xgb@3"
DS = f"wr:{TENANT_A}:dataset:dataset/ds-txn"


def _ctx():
    return CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": "u1", "scopes": ["*"]},
                   workspace_id=WORKSPACE, submitted_by="u1")


def _pipeline_env(event_type: str, run_urn: str) -> dict:
    return {
        "event_id": str(uuid.uuid4()), "event_type": event_type, "tenant_id": TENANT_A,
        "resource_urn": run_urn,
        "payload": {"pipeline_run_urn": run_urn, "output_storage_uri": "s3://x/y.parquet",
                    "snapshot_id": "snap-1", "row_count": 3},
    }


async def test_ac12_duplicate_succeeded_finalizes_exactly_once(container):
    add_input_dataset(container, urn=DS)
    job = await container.inference.submit(_ctx(), SubmitRequest(MODEL, DS))
    run_urn = job.pipeline_run_urn
    handler = PipelineEventHandler(container.inference)
    await handler.handle(_pipeline_env("pipeline.run.started", run_urn))
    # replay succeeded 3x
    for _ in range(3):
        await handler.handle(_pipeline_env("pipeline.run.succeeded", run_urn))
    succeeded = [e for _, e in container.memory_state.outbox
                 if e["event_type"] == "inference.job.succeeded"]
    assert len(succeeded) == 1
    assert len(container.memory_state.output_versions) == 1


async def test_pipeline_failed_marks_failed(container):
    add_input_dataset(container, urn=DS)
    job = await container.inference.submit(_ctx(), SubmitRequest(MODEL, DS))
    handler = PipelineEventHandler(container.inference)
    env = {
        "event_id": str(uuid.uuid4()), "event_type": "pipeline.run.failed",
        "tenant_id": TENANT_A, "resource_urn": job.pipeline_run_urn,
        "payload": {"pipeline_run_urn": job.pipeline_run_urn, "component_alias": "inference",
                    "message": "boom"},
    }
    await handler.handle(env)
    fetched = await container.inference.get(_ctx(), job.id)
    assert fetched.status == 7  # failed
    assert fetched.error["component_alias"] == "inference"


async def test_unknown_run_urn_ignored(container):
    handler = PipelineEventHandler(container.inference)
    # must not raise for a run that isn't ours
    await handler.handle(_pipeline_env("pipeline.run.succeeded",
                                       f"wr:{TENANT_A}:pipeline:run/not-ours"))


async def test_dataset_deleted_pauses_pinned_schedule(container):
    add_input_dataset(container, urn=DS)
    sch = await container.schedules.create(_ctx(), {
        "name": "pinned", "model_version_urn": MODEL, "input_selector": {"dataset_urn": DS},
        "output": {"dataset_name": "o"}, "interval_seconds": 3600})
    handler = DatasetEventHandler(container.inference)
    await handler.handle({
        "event_id": str(uuid.uuid4()), "event_type": "dataset.deleted", "tenant_id": TENANT_A,
        "resource_urn": DS, "payload": {"dataset_urn": DS}})
    fetched = await container.schedules.get(_ctx(), sch.id)
    assert fetched.enabled is False
    assert fetched.paused_reason == "INPUT_DELETED"


async def test_usage_budget_gate_blocks_and_restores(container):
    add_input_dataset(container, urn=DS)
    handler = UsageEventHandler(container.budget_gate)
    await handler.handle({"event_id": str(uuid.uuid4()), "event_type": "budget.exhausted",
                          "tenant_id": TENANT_A, "payload": {"meter": "inference_minutes"}})
    from app.domain.errors import RateLimited

    with pytest.raises(RateLimited):
        await container.inference.submit(_ctx(), SubmitRequest(MODEL, DS))
    await handler.handle({"event_id": str(uuid.uuid4()), "event_type": "budget.restored",
                          "tenant_id": TENANT_A, "payload": {"meter": "inference_minutes"}})
    job = await container.inference.submit(_ctx(), SubmitRequest(MODEL, DS))
    assert job.status in (2, 3)  # queued or submitted
