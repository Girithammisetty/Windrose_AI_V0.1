"""Integration (real Postgres + FORCE RLS): the bff batch IN-filters
(GET /runs?filter[experiment_id]=..., GET /models?filter[id]=...) return the id
set for the caller's tenant and NOTHING for another tenant."""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import TENANT_A, TENANT_B, auth, ctx_for, make_experiment, seed_finished_run

pytestmark = pytest.mark.integration


async def _finished(container, ctx, exp_id, rid):
    return await seed_finished_run(container, ctx, exp_id, mlflow_run_id=rid,
                                   metrics={"f1_score": 0.9})


async def test_batch_filters_tenant_isolated(client, container):
    ctx = ctx_for(TENANT_A)
    e1 = await make_experiment(container, ctx, name=f"b1-{uuid.uuid4().hex[:6]}")
    e2 = await make_experiment(container, ctx, name=f"b2-{uuid.uuid4().hex[:6]}")
    for e in (e1, e2):
        await _finished(container, ctx, e.id, f"r-{uuid.uuid4().hex[:6]}")
    run = await _finished(container, ctx, e1.id, f"r-{uuid.uuid4().hex[:6]}")
    reg = await container.registry_service.register(
        ctx, e1.id, run.id, {"model_name": f"bm-{uuid.uuid4().hex[:6]}"})

    # runs IN filter across two experiments (tenant A)
    ids = f"{e1.id},{e2.id}"
    resp = await client.get(f"/api/v1/runs?filter[experiment_id]={ids}", headers=auth(TENANT_A))
    assert resp.status_code == 200
    assert {r["experiment_id"] for r in resp.json()["data"]} == {e1.id, e2.id}
    # tenant B: same ids, RLS hides everything
    resp_b = await client.get(f"/api/v1/runs?filter[experiment_id]={ids}", headers=auth(TENANT_B))
    assert resp_b.status_code == 200 and resp_b.json()["data"] == []

    # models IN filter (tenant A) then tenant B
    m_resp = await client.get(f"/api/v1/models?filter[id]={reg['model_id']}",
                              headers=auth(TENANT_A))
    assert {m["id"] for m in m_resp.json()["data"]} == {reg["model_id"]}
    m_resp_b = await client.get(f"/api/v1/models?filter[id]={reg['model_id']}",
                                headers=auth(TENANT_B))
    assert m_resp_b.status_code == 200 and m_resp_b.json()["data"] == []
