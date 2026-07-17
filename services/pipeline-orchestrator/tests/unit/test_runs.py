"""Run lifecycle: submit, drive (via fake executor), terminate, quota, rate limit,
retry, manifest (PIPE-FR-030..040, AC-5/6/9/14)."""

from __future__ import annotations

import pytest

from app.domain.enums import RunStatus
from tests.conftest import TENANT_A, WORKSPACE, auth, create_template

pytestmark = pytest.mark.asyncio


async def _training_template(client, name="fraud-train"):
    body = {"workspace_id": WORKSPACE, "mode": "train",
            "dataset_refs": {"TRAIN": "wr:t:dataset:dataset/claims"},
            "parameters": {"label_column": "is_fraud"}, "name": name}
    r = await client.post("/api/v1/algorithm-templates/xgboost/pipelines", json=body,
                          headers=auth())
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


async def test_run_submits_202_then_drives_to_succeeded(client, container, fake_executor):
    tid = await _training_template(client)
    rows = [{"amount": 10, "is_fraud": "no"}, {"amount": 9999, "is_fraud": "yes"}]
    r = await client.post(f"/api/v1/pipelines/{tid}/run",
                          json={"run_parameters": {"training_data": rows,
                                                   "label_column": "is_fraud"}},
                          headers=auth())
    assert r.status_code == 202
    run_id = r.json()["data"]["id"]
    assert r.json()["operation_id"].startswith("op_")

    await container.run_service.drive_run(TENANT_A, run_id)
    got = (await client.get(f"/api/v1/runs/{run_id}", headers=auth())).json()["data"]
    assert got["status"] == "succeeded"
    assert got["model_uri"] and got["metrics"]["train_rows"] == 2.0
    assert fake_executor.specs[0].algorithm == "xgboost"
    # lifecycle events emitted
    types = {x["payload"]["event_type"] for x in container.memory_state.outbox}
    assert "pipeline.run.submitted" in types
    assert "pipeline.run.succeeded" in types
    assert "pipeline.run.output_registered" in types


async def test_failed_run_emits_failure_and_invalidation(client, tmp_path, clock):
    import httpx

    from app.container import build_container
    from app.main import create_app
    from tests.conftest import FakeExecutor, FakeMlflow, make_settings

    c = build_container(make_settings(tmp_path), mode="memory", clock=clock,
                        executor=FakeExecutor(fail=True), mlflow=FakeMlflow())
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as cl:
        tid = await _training_template(cl)
        r = await cl.post(f"/api/v1/pipelines/{tid}/run",
                          json={"run_parameters": {"training_data": [{"a": 1, "label": "x"}]}},
                          headers=auth())
        run_id = r.json()["data"]["id"]
        await c.run_service.drive_run(TENANT_A, run_id)
        got = (await cl.get(f"/api/v1/runs/{run_id}", headers=auth())).json()["data"]
        assert got["status"] == "failed"
        assert got["error"]["code"]
        types = {x["payload"]["event_type"] for x in c.memory_state.outbox}
        assert "pipeline.run.failed" in types
        assert "pipeline.run.outputs_invalidated" in types


async def test_ac14_model_type_not_runnable(client):
    from tests.conftest import data_prep_definition

    r = await create_template(client, name="composable", pipeline_type="model",
                              definition=data_prep_definition())
    tid = r.json()["data"]["id"]
    run = await client.post(f"/api/v1/pipelines/{tid}/run", json={"run_parameters": {}},
                            headers=auth())
    assert run.status_code == 422
    assert run.json()["error"]["code"] == "CANNOT_RUN_PIPELINE_TYPE"


async def test_ac9_rate_limit_second_run_429_with_retry_after(client, container, clock):
    tid = await _training_template(client)
    body = {"run_parameters": {"training_data": [{"a": 1, "label": "x"}]}}
    first = await client.post(f"/api/v1/pipelines/{tid}/run", json=body, headers=auth())
    assert first.status_code == 202
    clock.advance(seconds=5)
    second = await client.post(f"/api/v1/pipelines/{tid}/run", json=body, headers=auth())
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RATE_LIMITED"
    assert int(second.headers["Retry-After"]) >= 10


async def test_ac5_quota_queue_when_concurrency_exhausted(client, container, clock):
    # Set tenant quota to max 1 concurrent run.
    from app.domain.entities import CallCtx

    container.schedule_drive = lambda *a, **k: None  # observe queueing, not auto-drive
    await container.admin_service.set_quota(
        CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": "admin"}), TENANT_A,
        {"max_concurrent_runs": 1, "min_seconds_between_runs": 0})
    tid = await _training_template(client)
    body = {"run_parameters": {"training_data": [{"a": 1, "label": "x"}]}}
    r1 = await client.post(f"/api/v1/pipelines/{tid}/run", json=body, headers=auth())
    # first run is 'submitted' (active) but we did not drive it → still active
    r2 = await client.post(f"/api/v1/pipelines/{tid}/run", json=body, headers=auth())
    assert r2.json()["data"]["status"] == "quota_queued"
    run2_id = r2.json()["data"]["id"]
    # Completing run1 dequeues run2 → submitted.
    await container.run_service.drive_run(TENANT_A, r1.json()["data"]["id"])
    got2 = (await client.get(f"/api/v1/runs/{run2_id}", headers=auth())).json()["data"]
    assert got2["status"] in ("submitted", "running", "succeeded")


async def test_ac6_terminate_idempotent_single_cancel_event(client, container):
    from app.domain.entities import CallCtx

    container.schedule_drive = lambda *a, **k: None  # keep the run submitted for terminate
    await container.admin_service.set_quota(
        CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": "a"}), TENANT_A,
        {"max_concurrent_runs": 5, "min_seconds_between_runs": 0})
    tid = await _training_template(client)
    r = await client.post(f"/api/v1/pipelines/{tid}/run",
                          json={"run_parameters": {"training_data": [{"a": 1, "label": "x"}]}},
                          headers=auth())
    run_id = r.json()["data"]["id"]
    t1 = await client.put(f"/api/v1/runs/{run_id}/terminate", headers=auth())
    t2 = await client.put(f"/api/v1/runs/{run_id}/terminate", headers=auth())
    assert t1.status_code == 200 and t2.status_code == 200
    assert t1.json()["data"]["status"] == "cancelled"
    cancelled = [x for x in container.memory_state.outbox
                 if x["payload"]["event_type"] == "pipeline.run.cancelled"]
    assert len(cancelled) == 1


async def test_manifest_endpoint_redacts_secrets(client, container):
    tid = await _training_template(client)
    r = await client.post(f"/api/v1/pipelines/{tid}/run",
                          json={"run_parameters": {"training_data": [{"a": 1, "label": "x"}],
                                                   "api_token": "sekret"}},
                          headers=auth())
    run_id = r.json()["data"]["id"]
    m = (await client.get(f"/api/v1/runs/{run_id}/manifest", headers=auth())).json()["data"]
    assert m["manifest"] is not None
    assert m["resolved_parameters"]["api_token"] == "***"


_ = RunStatus
