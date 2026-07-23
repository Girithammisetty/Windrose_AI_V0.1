"""BRD 62 inc3 — a data-prep pipeline RUN executes the operator DAG locally over real
dataset rows and persists the computed output via the BRD 65 warehouse sink, ending
`succeeded` with real output_dataset_urns — the classic-pipeline run path, distinct
from training, with NO Argo.
"""

from __future__ import annotations

import httpx
import pytest

from app.adapters.dataset_reader import InMemoryDatasetReader
from app.container import build_container
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

DATASET = "wr:t:dataset:dataset/claims"


def _dataprep_definition():
    # read → filter (x >= 20) → select-columns([cat,x]) → write. Uses operators whose
    # params ARE declared in the catalog so the DAG validates like a UI submit.
    return {
        "metadata": {"description": "claims prep"},
        "nodes": [
            {"alias": "read_1", "component": "read-from-warehouse",
             "parameters": {"dataset": DATASET},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "flt", "component": "filter-data",
             "parameters": {"expression": "x >= 20"},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "sel", "component": "select-columns",
             "parameters": {"columns": ["cat", "x"]},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "write_1", "component": "write-to-warehouse",
             "parameters": {"output_dataset_name": "claims_prepped"}, "outputs": []}],
        "edges": [
            {"from": "read_1.out", "to": "flt.in1", "type": "dataframe"},
            {"from": "flt.out", "to": "sel.in1", "type": "dataframe"},
            {"from": "sel.out", "to": "write_1.in1", "type": "dataframe"}]}


async def _container(tmp_path, clock):
    rows = [{"cat": "a", "x": 10.0}, {"cat": "b", "x": 20.0},
            {"cat": "a", "x": 30.0}, {"cat": "b", "x": 40.0}]
    settings = make_settings(tmp_path, warehouse_sink="local")
    c = build_container(settings, mode="memory", clock=clock,
                        executor=FakeExecutor(), mlflow=FakeMlflow())
    # Seed the in-memory dataset reader the data-prep run reads its input rows from.
    c.run_service.d.dataset_reader = InMemoryDatasetReader({DATASET: rows})
    return c


async def test_dataprep_run_executes_locally_and_persists_output(tmp_path, clock):
    c = await _container(tmp_path, clock)
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as cl:
        r = await cl.post("/api/v1/pipelines", json={
            "workspace_id": WORKSPACE, "name": "claims-prep", "pipeline_type": "data_prep",
            "definition": _dataprep_definition()}, headers=auth())
        assert r.status_code == 201, r.text
        tid = r.json()["data"]["id"]

        run = await cl.post(f"/api/v1/pipelines/{tid}/run", json={"run_parameters": {}},
                            headers=auth())
        assert run.status_code == 202, run.text
        run_id = run.json()["data"]["id"]

        # Drive the run: real local operator DAG + sink persistence, NO Argo/executor.
        await c.run_service.drive_run(TENANT_A, run_id)

        got = (await cl.get(f"/api/v1/runs/{run_id}", headers=auth())).json()["data"]
        assert got["status"] == "succeeded", got
        # A real output dataset ref was persisted (not a model URI).
        assert got["output_dataset_urns"] and "warehouse/" in got["output_dataset_urns"][0]
        # Metrics reflect the computed result: x>=20 keeps 3 rows, select keeps 2 cols.
        assert got["metrics"]["outputs"] == 1.0
        assert got["metrics"]["output_rows"] == 3.0
        # Per-node component status recorded (read/filter/select/write all Succeeded).
        phases = {v["phase"] for v in got["components_status"].values()}
        assert phases == {"Succeeded"}
        # lifecycle events emitted
        types = {x["payload"]["event_type"] for x in c.memory_state.outbox}
        assert "pipeline.run.succeeded" in types


async def test_dataprep_run_fails_closed_on_bad_operator(tmp_path, clock):
    c = await _container(tmp_path, clock)
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    bad = _dataprep_definition()
    bad["nodes"][1]["parameters"] = {"expression": "nonexistent_col > 5"}  # bad filter
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as cl:
        r = await cl.post("/api/v1/pipelines", json={
            "workspace_id": WORKSPACE, "name": "claims-bad", "pipeline_type": "data_prep",
            "definition": bad}, headers=auth())
        tid = r.json()["data"]["id"]
        run = await cl.post(f"/api/v1/pipelines/{tid}/run", json={"run_parameters": {}},
                            headers=auth())
        run_id = run.json()["data"]["id"]
        await c.run_service.drive_run(TENANT_A, run_id)
        got = (await cl.get(f"/api/v1/runs/{run_id}", headers=auth())).json()["data"]
        assert got["status"] == "failed"  # surfaced, never a silent success
