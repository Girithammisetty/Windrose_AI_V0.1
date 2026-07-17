"""Job submission, lifecycle, concurrency, cancel/retry, reserved-endpoint tests.

Covers AC-1, AC-3, AC-4 (memory), AC-5, AC-6, AC-7, AC-11, AC-15.
"""

from __future__ import annotations

import pytest

from app.container import build_container
from app.domain.ports import CallCtx
from app.domain.schema_compat import ModelInputColumn
from tests.conftest import (
    TENANT_A,
    WORKSPACE,
    FakeExecutor,
    FakeRegistry,
    add_input_dataset,
    auth,
    make_settings,
)

MODEL = "wr:11111111-1111-4111-8111-111111111111:experiment:model_version/fraud-xgb@3"
DS = "wr:11111111-1111-4111-8111-111111111111:dataset:dataset/ds-txn"


def _ctx(scopes=None):
    return CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": "u1",
                   "scopes": scopes or ["*"]}, workspace_id=WORKSPACE, submitted_by="u1")


async def test_ac3_compatible_submit_flows_to_submitted(container, client):
    add_input_dataset(container, urn=DS)
    resp = await client.post("/api/v1/inferences",
                             json={"model_version_urn": MODEL, "input_dataset_urn": DS},
                             headers=auth())
    assert resp.status_code == 202, resp.text
    body = resp.json()["data"]
    assert body["status"] == "submitted"
    submitted = container.memory_state.outbox
    types = [e["event_type"] for _, e in submitted]
    assert "inference.job.created" in types
    assert "inference.job.submitted" in types


async def test_ac1_incompatible_submit_rejected_no_run(container, client, executor):
    # dataset lacking merchant_id -> SCHEMA_INCOMPATIBLE, no pipeline run
    add_input_dataset(container, urn=DS, schema={
        "amount": {"type": "double", "nullable": False},
        "age": {"type": "long", "nullable": False}})
    resp = await client.post("/api/v1/inferences",
                             json={"model_version_urn": MODEL, "input_dataset_urn": DS},
                             headers=auth())
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "SCHEMA_INCOMPATIBLE"
    assert any(d["name"] == "merchant_id" and d["verdict"] == "missing"
               for d in err["details"])
    assert executor.runs == []  # never scored


async def test_validate_endpoint_lists_all_violations(container, client):
    add_input_dataset(container, urn=DS, schema={"age": {"type": "string", "nullable": False}})
    resp = await client.post("/api/v1/inferences/validate",
                             json={"model_version_urn": MODEL, "input_dataset_urn": DS},
                             headers=auth())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["compatible"] is False
    verdicts = {c["name"]: c["verdict"] for c in data["columns"]}
    assert verdicts["age"] == "type_mismatch"
    assert verdicts["merchant_id"] == "missing"


async def test_ac4_full_lifecycle_success_with_output_and_lineage(container, client):
    add_input_dataset(container, urn=DS)
    resp = await client.post("/api/v1/inferences",
                             json={"model_version_urn": MODEL, "input_dataset_urn": DS},
                             headers=auth())
    job_id = resp.json()["data"]["job_id"]
    await container.inference.execute_job(TENANT_A, job_id)

    got = await client.get(f"/api/v1/inferences/{job_id}", headers=auth())
    data = got.json()["data"]
    assert data["status"] == "succeeded"
    assert data["output_dataset"]["urn"] is not None
    assert data["output_dataset"]["version"] == 1
    # default name convention <model>-v<version>-scores
    types = [e["event_type"] for _, e in container.memory_state.outbox]
    assert "inference.job.succeeded" in types
    # lineage edges model->job, input->job, job->output resolve
    jurn = f"wr:{TENANT_A}:inference:job/{job_id}"
    lin = await client.get("/api/v1/lineage", params={"urn": jurn}, headers=auth())
    activities = {e["activity"] for e in lin.json()["data"]["edges"]}
    assert {"used_by", "input_to", "produced"} <= activities


async def test_ac5_failed_run_no_output_registered(clock, registry):
    executor = FakeExecutor(fail=True)
    container = build_container(make_settings(), mode="memory", clock=clock,
                               registry=registry, executor=executor)
    add_input_dataset(container, urn=DS)
    ctx = _ctx()
    from app.domain.services import SubmitRequest

    job = await container.inference.submit(ctx, SubmitRequest(MODEL, DS))
    await container.inference.execute_job(TENANT_A, job.id)
    fetched = await container.inference.get(ctx, job.id)
    assert fetched.status == 7  # failed
    assert fetched.error["code"] == "PIPELINE_FAILED"
    assert fetched.output_dataset_urn is None


async def test_ac6_stage_denied_and_permission(clock, executor):
    registry = FakeRegistry()
    registry.add("legacy", 1, stage="archived", inputs=[
        ModelInputColumn("amount", "double", required=False)])
    container = build_container(make_settings(), mode="memory", clock=clock,
                               registry=registry, executor=executor)
    urn = f"wr:{TENANT_A}:experiment:model_version/legacy@1"
    add_input_dataset(container, urn=DS, schema={"amount": {"type": "double", "nullable": False}})
    from app.domain.errors import ModelStageDenied, PermissionDenied
    from app.domain.services import SubmitRequest

    with pytest.raises(ModelStageDenied):
        await container.inference.submit(_ctx(["inference.job.create"]),
                                         SubmitRequest(urn, DS))
    # with the flag but without create_unpromoted permission -> 403
    with pytest.raises(PermissionDenied):
        await container.inference.submit(
            _ctx(["inference.job.submit"]),
            SubmitRequest(urn, DS, allow_unpromoted=True))


async def test_ac7_concurrency_queue_and_dequeue(clock, registry, executor):
    settings = make_settings().model_copy(update={"max_concurrent_inference_jobs": 2})
    container = build_container(settings, mode="memory", clock=clock, registry=registry,
                               executor=executor)
    add_input_dataset(container, urn=DS)
    ctx = _ctx()
    from app.domain.enums import JobStatus
    from app.domain.services import SubmitRequest

    j1 = await container.inference.submit(ctx, SubmitRequest(MODEL, DS, name="j1"))
    j2 = await container.inference.submit(ctx, SubmitRequest(MODEL, DS, name="j2"))
    assert j1.status == int(JobStatus.submitted)
    assert j2.status == int(JobStatus.submitted)
    j3 = await container.inference.submit(ctx, SubmitRequest(MODEL, DS, name="j3"))
    assert j3.status == int(JobStatus.queued)
    # completing j1 frees a slot -> j3 auto-submits
    await container.inference.execute_job(TENANT_A, j1.id)
    fetched = await container.inference.get(ctx, j3.id)
    assert fetched.status == int(JobStatus.submitted)


async def test_ac11_cancel_is_idempotent(container, client):
    add_input_dataset(container, urn=DS)
    # cap so the job stays submitted (not auto-run in memory unit tests)
    ctx = _ctx()
    from app.domain.enums import JobStatus
    from app.domain.services import SubmitRequest

    job = await container.inference.submit(ctx, SubmitRequest(MODEL, DS, name="cancelme"))
    assert job.status == int(JobStatus.submitted)
    r1 = await client.post(f"/api/v1/inferences/{job.id}/cancel", headers=auth())
    assert r1.status_code == 200
    r2 = await client.post(f"/api/v1/inferences/{job.id}/cancel", headers=auth())
    assert r2.status_code == 200  # idempotent
    fetched = await container.inference.get(ctx, job.id)
    assert fetched.status == int(JobStatus.cancelled)


async def test_retry_creates_new_job_linked(container):
    add_input_dataset(container, urn=DS)
    executor = FakeExecutor(fail=True)
    container = build_container(make_settings(), mode="memory",
                               registry=container.registry, executor=executor)
    add_input_dataset(container, urn=DS)
    ctx = _ctx()
    from app.domain.services import SubmitRequest

    job = await container.inference.submit(ctx, SubmitRequest(MODEL, DS, name="r1"))
    await container.inference.execute_job(TENANT_A, job.id)
    retried = await container.inference.retry(ctx, job.id)
    assert retried.id != job.id
    assert retried.retried_from_job_id == job.id


async def test_ac15_reserved_endpoints_501(client):
    for method, url in [("GET", "/api/v1/endpoints"),
                        ("POST", "/api/v1/endpoints"),
                        ("GET", "/api/v1/endpoints/e1"),
                        ("POST", "/api/v1/endpoints/e1/predict")]:
        resp = await client.request(method, url, json={}, headers=auth())
        assert resp.status_code == 501, f"{method} {url} -> {resp.status_code}"
        assert resp.json()["error"]["code"] == "NOT_IMPLEMENTED"
