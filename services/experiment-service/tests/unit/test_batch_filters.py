"""Batch IN-filter endpoints for the bff dataloaders (N+1 avoidance):
GET /runs?filter[experiment_id]=e1,e2,e3 and GET /models?filter[id]=m1,m2.
Both stay tenant-isolated and capped at 200 ids."""

from __future__ import annotations

from tests.conftest import TENANT_B, auth, ctx_for, make_experiment, seed_finished_run


async def _seed_run(container, ctx, exp_id, mlflow_run_id):
    return await seed_finished_run(container, ctx, exp_id, mlflow_run_id=mlflow_run_id,
                                   metrics={"f1_score": 0.9})


async def test_runs_in_filter_across_experiments(client, container):
    ctx = ctx_for()
    exps = [await make_experiment(container, ctx, name=f"e{i}") for i in range(4)]
    for i, e in enumerate(exps):
        await _seed_run(container, ctx, e.id, f"r{i}")

    ids = ",".join(e.id for e in exps[:3])
    resp = await client.get(f"/api/v1/runs?filter[experiment_id]={ids}", headers=auth())
    assert resp.status_code == 200, resp.text
    runs = resp.json()["data"]
    returned_experiments = {r["experiment_id"] for r in runs}
    assert returned_experiments == {exps[0].id, exps[1].id, exps[2].id}
    assert exps[3].id not in returned_experiments  # 4th experiment excluded


async def test_runs_in_filter_tenant_isolated(client, container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx, name="iso-runs")
    await _seed_run(container, ctx, exp.id, "riso")
    # tenant B asks for tenant A's experiment ids -> sees nothing
    resp = await client.get(f"/api/v1/runs?filter[experiment_id]={exp.id}",
                            headers=auth(TENANT_B))
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_models_in_filter(client, container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx, name="mbatch")
    mids = []
    for i in range(3):
        run = await _seed_run(container, ctx, exp.id, f"mr{i}")
        reg = await container.registry_service.register(ctx, exp.id, run.id,
                                                        {"model_name": f"mdl{i}"})
        mids.append(reg["model_id"])

    ids = ",".join(mids[:2])
    resp = await client.get(f"/api/v1/models?filter[id]={ids}", headers=auth())
    assert resp.status_code == 200, resp.text
    returned = {m["id"] for m in resp.json()["data"]}
    assert returned == {mids[0], mids[1]}
    assert mids[2] not in returned


async def test_models_in_filter_tenant_isolated(client, container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx, name="iso-models")
    run = await _seed_run(container, ctx, exp.id, "misor")
    reg = await container.registry_service.register(ctx, exp.id, run.id, {"model_name": "isom"})
    resp = await client.get(f"/api/v1/models?filter[id]={reg['model_id']}",
                            headers=auth(TENANT_B))
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_batch_id_cap_enforced(client):
    too_many = ",".join(str(i) for i in range(201))
    resp = await client.get(f"/api/v1/runs?filter[experiment_id]={too_many}", headers=auth())
    assert resp.status_code == 422
