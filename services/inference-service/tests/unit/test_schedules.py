"""Scheduled scoring unit tests (INF-FR-050..055, AC-8, AC-9, AC-10)."""

from __future__ import annotations

from app.domain.enums import JobStatus
from app.domain.ports import CallCtx
from app.domain.schema_compat import ModelInputColumn
from tests.conftest import TENANT_A, WORKSPACE, add_input_dataset

MODEL_V = f"wr:{TENANT_A}:experiment:model_version/fraud-xgb@3"
MODEL_URN = f"wr:{TENANT_A}:experiment:model/fraud-xgb"
DS = f"wr:{TENANT_A}:dataset:dataset/ds-txn"


def _ctx():
    return CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": "mle", "scopes": ["*"]},
                   workspace_id=WORKSPACE, submitted_by="mle")


def _base(**over):
    body = {"name": "nightly", "model_version_urn": MODEL_V,
            "input_selector": {"dataset_urn": DS}, "output": {"dataset_name": "sched-scores"},
            "interval_seconds": 3600}
    body.update(over)
    return body


async def test_create_defaults_output_append(container):
    add_input_dataset(container, urn=DS)
    sch = await container.schedules.create(_ctx(), _base())
    assert sch.output["mode"] == "append"
    assert sch.enabled is True
    assert sch.next_fire_at is not None


async def test_ac8_overlap_skip_emits_fire_skipped(container):
    add_input_dataset(container, urn=DS)
    sch = await container.schedules.create(_ctx(), _base(overlap_policy="skip"))
    first = await container.schedules.fire(sch)
    assert first["fired"] is True
    # previous job is still non-terminal (submitted) -> next fire skips
    second = await container.schedules.fire(sch)
    assert second == {"fired": False, "reason": "OVERLAP"}
    skipped = [e for _, e in container.memory_state.outbox
               if e["event_type"] == "inference.schedule.fire_skipped"]
    assert any(e["payload"]["reason"] == "OVERLAP" for e in skipped)


async def test_ac9_stage_selector_resolves_and_skips_when_absent(container, registry):
    add_input_dataset(container, urn=DS)
    sch = await container.schedules.create(
        _ctx(), _base(name="stagesched", model_version_urn=None, model_urn=MODEL_URN,
                      stage_selector="production"))
    fired = await container.schedules.fire(sch)
    assert fired["fired"] is True
    await container.inference.execute_job(TENANT_A, fired["job_id"])  # clear overlap
    # remove the production version -> next fire skips NO_MODEL_IN_STAGE
    registry.by_stage.pop(("fraud-xgb", "production"))
    result = await container.schedules.fire(sch)
    assert result["reason"] == "NO_MODEL_IN_STAGE"


async def test_ac10_circuit_breaker_auto_pauses(container):
    # input_selector without dataset_urn -> INPUT_RESOLUTION_FAILED each fire
    sch = await container.schedules.create(
        _ctx(), _base(name="breaker", input_selector={"partition_window": {"range": "prev"}}))
    for _ in range(3):
        await container.schedules.trigger(_ctx(), sch.id)
    paused = await container.schedules.get(_ctx(), sch.id)
    assert paused.enabled is False
    assert paused.paused_reason == "AUTO_PAUSED_CONSECUTIVE_FAILURES"
    auto = [e for _, e in container.memory_state.outbox
            if e["event_type"] == "inference.schedule.auto_paused"]
    assert auto
    # resume resets the counter and re-enables
    resumed = await container.schedules.resume(_ctx(), sch.id)
    assert resumed.enabled is True
    assert resumed.consecutive_failures == 0


async def test_pause_resume(container):
    add_input_dataset(container, urn=DS)
    sch = await container.schedules.create(_ctx(), _base(name="pr"))
    paused = await container.schedules.pause(_ctx(), sch.id)
    assert paused.enabled is False
    assert paused.next_fire_at is None
    resumed = await container.schedules.resume(_ctx(), sch.id)
    assert resumed.enabled is True
    assert resumed.next_fire_at is not None


async def test_cancel_running_overlap_policy(container):
    add_input_dataset(container, urn=DS)
    sch = await container.schedules.create(_ctx(), _base(name="cr",
                                           overlap_policy="cancel_running"))
    first = await container.schedules.fire(sch)
    prev_job_id = first["job_id"]
    await container.schedules.fire(sch)
    prev = await container.inference.get(_ctx(), prev_job_id)
    assert prev.status in (int(JobStatus.cancelling), int(JobStatus.cancelled))


async def test_br7_queue_policy_allows_at_most_one_pending_fire(registry, executor):
    from app.container import build_container
    from app.domain.enums import JobStatus
    from tests.conftest import make_settings

    settings = make_settings().model_copy(update={"max_concurrent_inference_jobs": 1})
    container = build_container(settings, mode="memory", registry=registry, executor=executor)
    add_input_dataset(container, urn=DS)
    sch = await container.schedules.create(_ctx(), _base(name="qsched", overlap_policy="queue"))

    first = await container.schedules.fire(sch)   # -> submitted (active=1)
    assert first["fired"] is True
    second = await container.schedules.fire(sch)  # -> queued (the single pending)
    assert second["fired"] is True
    assert second["status"] == int(JobStatus.queued)
    third = await container.schedules.fire(sch)   # -> skip, one already pending
    assert third == {"fired": False, "reason": "OVERLAP"}

    from app.domain.ports import Filters

    async with container.deps.uow_factory(TENANT_A) as uow:
        pending = await uow.jobs.list(
            Filters(schedule_id=sch.id, status=int(JobStatus.queued)), "-created_at", 10, None)
    assert len(pending.items) == 1


def test_model_input_column_helper():
    col = ModelInputColumn("x", "double")
    assert col.required is True
