"""Tenant isolation + authz matrix (MASTER-FR-004, AC-10)."""

from __future__ import annotations

import pytest

from tests.conftest import TENANT_A, TENANT_B, auth, create_template

pytestmark = pytest.mark.asyncio


async def test_ac10_cross_tenant_run_returns_404(client, container):
    # Tenant A creates + runs; tenant B may not read the run.
    from tests.conftest import WORKSPACE

    body = {"workspace_id": WORKSPACE, "mode": "train",
            "dataset_refs": {"TRAIN": "wr:t:dataset:dataset/x"},
            "parameters": {"label_column": "label"}, "name": "iso"}
    tid = (await client.post("/api/v1/algorithm-templates/xgboost/pipelines", json=body,
                             headers=auth(TENANT_A))).json()["data"]["id"]
    r = await client.post(f"/api/v1/pipelines/{tid}/run",
                          json={"run_parameters": {"training_data": [{"a": 1, "label": "x"}]}},
                          headers=auth(TENANT_A))
    run_id = r.json()["data"]["id"]
    assert (await client.get(f"/api/v1/runs/{run_id}",
                             headers=auth(TENANT_A))).status_code == 200
    assert (await client.get(f"/api/v1/runs/{run_id}",
                             headers=auth(TENANT_B))).status_code == 404


async def test_cross_tenant_template_404(client):
    tid = (await create_template(client, tenant=TENANT_A, name="a-only")).json()["data"]["id"]
    assert (await client.get(f"/api/v1/pipelines/{tid}",
                             headers=auth(TENANT_B))).status_code == 404


async def test_missing_scope_denied(client):
    # Token without the create scope is denied (403).
    r = await create_template(client, name="noscope",
                              **{})  # default scopes=["*"] -> allow
    assert r.status_code == 201
    denied = await client.post(
        "/api/v1/pipelines",
        json={"workspace_id": "w", "name": "x", "pipeline_type": "data_prep",
              "definition": {"nodes": [], "edges": []}},
        headers=auth(scopes=["pipeline.run.read"]))
    assert denied.status_code == 403


async def test_missing_bearer_401(client):
    r = await client.get("/api/v1/pipelines")
    assert r.status_code == 401


async def test_alg_none_token_rejected(client):
    import jwt as pyjwt

    tok = pyjwt.encode({"sub": "x", "tenant_id": TENANT_A, "iss": "i", "aud": "a",
                        "exp": 9999999999}, key="", algorithm="none")
    r = await client.get("/api/v1/pipelines", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401
