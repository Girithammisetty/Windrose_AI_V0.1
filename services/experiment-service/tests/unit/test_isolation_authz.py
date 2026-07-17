"""MASTER-FR-003/012, AC-12: cross-tenant isolation (unit variant) + authz."""

from __future__ import annotations

from tests.conftest import (
    PIPE_FE,
    PIPE_MODEL,
    PIPE_TRAIN,
    TENANT_B,
    WORKSPACE,
    auth,
)


def _body(name="Iso"):
    return {"workspace_id": WORKSPACE, "name": name, "model_type": "classification",
            "model_pipeline_urn": PIPE_MODEL, "feature_engineering_pipeline_urn": PIPE_FE,
            "training_pipeline_urn": PIPE_TRAIN}


async def test_cross_tenant_get_is_404(client):
    created = (await client.post("/api/v1/experiments", json=_body(), headers=auth())).json()
    exp_id = created["data"]["id"]
    # tenant B cannot see tenant A's experiment
    resp = await client.get(f"/api/v1/experiments/{exp_id}", headers=auth(TENANT_B))
    assert resp.status_code == 404


async def test_missing_scope_is_403(client):
    resp = await client.post(
        "/api/v1/experiments", json=_body("NoScope"),
        headers=auth(scopes=["experiment.experiment.read"]))
    assert resp.status_code == 403


async def test_missing_token_is_401(client):
    resp = await client.get("/api/v1/experiments")
    assert resp.status_code == 401
