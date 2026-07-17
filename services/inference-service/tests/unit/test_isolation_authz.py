"""Tenant-isolation + authz-matrix unit variant (MASTER-FR-001/004, AC-13)."""

from __future__ import annotations

from tests.conftest import TENANT_A, TENANT_B, add_input_dataset, auth

MODEL = f"wr:{TENANT_A}:experiment:model_version/fraud-xgb@3"
DS = f"wr:{TENANT_A}:dataset:dataset/ds-txn"


async def _submit(container, client) -> str:
    add_input_dataset(container, urn=DS)
    resp = await client.post("/api/v1/inferences",
                             json={"model_version_urn": MODEL, "input_dataset_urn": DS},
                             headers=auth(TENANT_A))
    assert resp.status_code == 202, resp.text
    return resp.json()["data"]["job_id"]


async def test_ac13_cross_tenant_job_access_404(container, client):
    job_id = await _submit(container, client)
    for method, url in [("GET", f"/api/v1/inferences/{job_id}"),
                        ("POST", f"/api/v1/inferences/{job_id}/cancel"),
                        ("POST", f"/api/v1/inferences/{job_id}/retry"),
                        ("DELETE", f"/api/v1/inferences/{job_id}")]:
        resp = await client.request(method, url, headers=auth(TENANT_B))
        assert resp.status_code == 404, f"{method} {url} -> {resp.status_code}"


async def test_list_never_shows_foreign_jobs(container, client):
    await _submit(container, client)
    resp = await client.get("/api/v1/inferences", headers=auth(TENANT_B))
    assert resp.json()["data"] == []


async def test_authz_matrix_missing_scope_403(container, client):
    add_input_dataset(container, urn=DS)
    # token lacking inference.job.read
    resp = await client.get("/api/v1/inferences",
                            headers=auth(TENANT_A, scopes=["inference.job.create"]))
    assert resp.status_code == 403
    # submit needs inference.job.create; a read-only token is denied
    resp = await client.post("/api/v1/inferences",
                             json={"model_version_urn": MODEL, "input_dataset_urn": DS},
                             headers=auth(TENANT_A, scopes=["inference.job.read"]))
    assert resp.status_code == 403


async def test_missing_bearer_401(client):
    resp = await client.get("/api/v1/inferences")
    assert resp.status_code == 401
