"""Argo backend selection (LOW fix) + component error enrichment AC-7."""

from __future__ import annotations

import httpx
import pytest

from app.container import build_container
from app.domain.enums import RunStatus
from app.main import create_app
from tests.conftest import (
    TENANT_A,
    WORKSPACE,
    FakeExecutor,
    FakeMlflow,
    auth,
    make_settings,
)

pytestmark = pytest.mark.asyncio


async def _training_template(cl):
    body = {"workspace_id": WORKSPACE, "mode": "train",
            "dataset_refs": {"TRAIN": "wr:t:dataset:dataset/x"},
            "parameters": {"label_column": "label"}, "name": "t"}
    r = await cl.post("/api/v1/algorithm-templates/xgboost/pipelines", json=body,
                      headers=auth())
    return r.json()["data"]["id"]


async def test_argo_backend_selection_is_real_not_ignored(tmp_path, clock):
    # executor_backend="argo" must actually instantiate the Argo executor and attempt
    # a real submit — which raises DependencyUnavailable with no k8s (infra-gated).
    settings = make_settings(tmp_path, executor_backend="argo",
                             argo_server_url="http://localhost:2")  # unreachable
    c = build_container(settings, mode="memory", clock=clock,
                        executor=FakeExecutor(), mlflow=FakeMlflow())
    assert c.deps.workflow_backend is not None
    assert type(c.deps.workflow_backend).__name__ == "ArgoWorkflowExecutor"
    c.schedule_drive = lambda *a, **k: None
    app = create_app(c)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as cl:
        tid = await _training_template(cl)
        r = await cl.post(f"/api/v1/pipelines/{tid}/run",
                          json={"run_parameters": {"training_data": [{"a": 1, "label": "x"}]}},
                          headers=auth())
        run_id = r.json()["data"]["id"]
        final = await c.run_service.drive_run(TENANT_A, run_id)
        assert final.status == int(RunStatus.failed)
        assert final.error["code"] == "DEPENDENCY_UNAVAILABLE"


async def test_ac7_oom_component_error_enriched(container, client):
    container.schedule_drive = lambda *a, **k: None
    tid = await _training_template(client)
    r = await client.post(f"/api/v1/pipelines/{tid}/run",
                          json={"run_parameters": {"training_data": [{"a": 1, "label": "x"}]}},
                          headers=auth())
    run = r.json()["data"]
    ok = await container.run_service.record_component_error(
        TENANT_A, run["argo_workflow_name"],
        {"tenant_id": TENANT_A, "title": "pod killed",
         "detail": "Container train-1 terminated: OOMKilled", "alias": "train-1"})
    assert ok
    got = (await client.get(f"/api/v1/runs/{run['id']}", headers=auth())).json()["data"]
    assert got["error"]["code"] == "OUT_OF_MEMORY"
    assert got["error"]["alias"] == "train-1"


async def test_component_timeout_enrichment(container):
    container.schedule_drive = lambda *a, **k: None
    from app.domain.entities import CallCtx

    ctx = CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": "u"},
                  workspace_id=WORKSPACE)
    template, _ = await container.instantiation_service.instantiate_pipeline(
        ctx, "random_forest", mode="train", dataset_refs={"TRAIN": "wr:t:dataset:dataset/x"},
        params={}, workspace_id=WORKSPACE, name="rf-timeout")
    _, run = await container.run_service.create_run(
        ctx, template.id, {"training_data": [{"a": 1, "label": "x"}]})
    await container.run_service.record_component_error(
        TENANT_A, run.argo_workflow_name,
        {"tenant_id": TENANT_A, "title": "deadline",
         "detail": "Pod was active on the node longer than the specified deadline",
         "alias": "train-1"})
    got = await container.run_service.get(ctx, run.id)
    assert got.error["code"] == "COMPONENT_TIMEOUT"
