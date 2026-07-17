"""Reaper (INF-FR-042/BR-12) and finalize-failure (INF-FR-032) unit tests."""

from __future__ import annotations

import pytest

from app.container import build_container
from app.domain.enums import JobStatus
from app.domain.ports import CallCtx
from app.domain.services import SubmitRequest
from tests.conftest import TENANT_A, WORKSPACE, FakeClock, add_input_dataset, make_settings

MODEL = f"wr:{TENANT_A}:experiment:model_version/fraud-xgb@3"
DS = f"wr:{TENANT_A}:dataset:dataset/ds-txn"


def _ctx():
    return CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": "u1", "scopes": ["*"]},
                   workspace_id=WORKSPACE, submitted_by="u1")


def _cap1_container(registry, executor, clock):
    settings = make_settings().model_copy(update={"max_concurrent_inference_jobs": 1})
    return build_container(settings, mode="memory", clock=clock, registry=registry,
                          executor=executor)


async def test_queued_job_reaped_at_queue_timeout(registry, executor):
    clock = FakeClock()
    container = _cap1_container(registry, executor, clock)
    add_input_dataset(container, urn=DS)
    j1 = await container.inference.submit(_ctx(), SubmitRequest(MODEL, DS, name="j1"))
    j2 = await container.inference.submit(_ctx(), SubmitRequest(MODEL, DS, name="j2"))
    assert j1.status == int(JobStatus.submitted)
    assert j2.status == int(JobStatus.queued)

    clock.advance(minutes=61)  # past the 60-min queued timeout, well under 8h run window
    reaped = await container.inference.reap(TENANT_A)
    assert reaped == 1
    j2f = await container.inference.get(_ctx(), j2.id)
    assert j2f.status == int(JobStatus.failed)
    assert j2f.error["code"] == "QUOTA_TIMEOUT"
    # the running job is NOT reaped at 61 min (its window is 8h)
    j1f = await container.inference.get(_ctx(), j1.id)
    assert j1f.status == int(JobStatus.submitted)


async def test_running_job_reaped_at_run_timeout(registry, executor):
    clock = FakeClock()
    container = _cap1_container(registry, executor, clock)
    add_input_dataset(container, urn=DS)
    j1 = await container.inference.submit(_ctx(), SubmitRequest(MODEL, DS, name="j1"))
    clock.advance(hours=9)
    reaped = await container.inference.reap(TENANT_A)
    assert reaped == 1
    j1f = await container.inference.get(_ctx(), j1.id)
    assert j1f.status == int(JobStatus.failed)
    assert j1f.error["code"] == "QUOTA_TIMEOUT"


async def test_lineage_failure_surfaced_not_swallowed(container):
    """INF-FR-032: a lineage-write failure must surface as
    failed(LINEAGE_REGISTRATION_FAILED) with the output still registered — never
    swallowed or rolled back to running."""
    add_input_dataset(container, urn=DS)

    async def boom(*_a, **_k):
        raise RuntimeError("lineage store down")

    container.inference._write_lineage = boom  # inject fault
    job = await container.inference.submit(_ctx(), SubmitRequest(MODEL, DS))
    await container.inference.execute_job(TENANT_A, job.id)

    fetched = await container.inference.get(_ctx(), job.id)
    assert fetched.status == int(JobStatus.failed)
    assert fetched.error["code"] == "LINEAGE_REGISTRATION_FAILED"
    # output dataset version remains registered (no-partial preserved, dataset flagged)
    async with container.deps.uow_factory(TENANT_A) as uow:
        assert await uow.outputs.version_for_job(job.id) is not None
    types = [e["event_type"] for _, e in container.memory_state.outbox]
    assert "inference.job.failed" in types
    assert "inference.job.succeeded" not in types


@pytest.mark.parametrize("attempts", [3])
async def test_finalize_retries_then_succeeds(container, attempts):
    """Transient lineage error recovers within the retry budget -> succeeded."""
    add_input_dataset(container, urn=DS)
    calls = {"n": 0}
    real = container.inference._write_lineage

    async def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return await real(*a, **k)

    container.inference._write_lineage = flaky
    job = await container.inference.submit(_ctx(), SubmitRequest(MODEL, DS))
    await container.inference.execute_job(TENANT_A, job.id)
    fetched = await container.inference.get(_ctx(), job.id)
    assert fetched.status == int(JobStatus.succeeded)
    assert calls["n"] >= 2
