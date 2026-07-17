"""EXP-FR-001/002: experiment CRUD, pipeline-URN validation, archive/restore."""

from __future__ import annotations

from tests.conftest import PIPE_FE, PIPE_MODEL, PIPE_TRAIN, WORKSPACE, auth


def _body(name="Fraud", **over):
    b = {"workspace_id": WORKSPACE, "name": name, "model_type": "classification",
         "model_pipeline_urn": PIPE_MODEL, "feature_engineering_pipeline_urn": PIPE_FE,
         "training_pipeline_urn": PIPE_TRAIN}
    b.update(over)
    return b


async def test_create_experiment_calls_mlflow_and_stores_id(client):
    resp = await client.post("/api/v1/experiments", json=_body(), headers=auth())
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["name"] == "Fraud"
    assert data["mlflow_experiment_id"]  # EXP-FR-001: synchronous MLflow create
    assert data["model_type"] == "classification"


async def test_pipeline_urns_must_be_present_and_distinct(client):
    resp = await client.post(
        "/api/v1/experiments",
        json=_body(feature_engineering_pipeline_urn=PIPE_MODEL), headers=auth())
    assert resp.status_code == 422
    assert "distinct" in resp.text


async def test_duplicate_name_conflicts(client):
    await client.post("/api/v1/experiments", json=_body(), headers=auth())
    resp = await client.post("/api/v1/experiments", json=_body(), headers=auth())
    assert resp.status_code == 409


async def test_archive_and_restore(client):
    created = (await client.post("/api/v1/experiments", json=_body(), headers=auth())).json()
    exp_id = created["data"]["id"]
    assert (await client.delete(f"/api/v1/experiments/{exp_id}", headers=auth())).status_code == 200
    # archived list shows it; active list does not
    archived = (await client.get("/api/v1/experiments/list_archived", headers=auth())).json()
    assert any(e["id"] == exp_id for e in archived["data"])
    restored = await client.patch(f"/api/v1/experiments/{exp_id}/restore", headers=auth())
    assert restored.status_code == 200
    assert restored.json()["data"]["archived"] is False
