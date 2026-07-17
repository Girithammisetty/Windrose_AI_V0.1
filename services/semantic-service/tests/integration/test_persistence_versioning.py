"""SQL-tier persistence: model lifecycle, immutable versions, projections,
outbox + dispatcher, idempotency, pgvector search (Testcontainers PG)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import (
    TENANT_A,
    WORKSPACE,
    auth,
    create_model,
    create_published_model,
    publish_model,
)
from tests.unit.test_verified_queries import approve_vq, create_vq

pytestmark = pytest.mark.integration


async def test_full_model_lifecycle_persists(client, container):
    model = await create_model(client)
    await publish_model(client, model["id"])

    resp = await client.get(f"/api/v1/models/{model['id']}", headers=auth())
    data = resp.json()["data"]
    assert data["published_version_no"] == 1

    # compile against the published version through the SQL store
    resp = await client.post(
        "/api/v1/compile",
        json={"model": "sales", "workspace_id": WORKSPACE, "dialect": "trino",
              "metrics": ["revenue"], "dimensions": ["region"]},
        headers=auth())
    assert resp.status_code == 200
    assert 'sum("o"."order_total")' in resp.json()["data"]["sql"]


async def test_projections_rebuilt_on_publish(client, container, engine):
    await create_published_model(client)
    session_factory = container.extras["session_factory"]
    async with session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A})
        measures = (await session.execute(
            text("SELECT name, agg FROM measures ORDER BY name"))).all()
        entities = (await session.execute(
            text("SELECT name, dataset_urn FROM entities ORDER BY name"))).all()
        joins = (await session.execute(
            text("SELECT name, cardinality FROM join_paths"))).all()
    names = {m[0] for m in measures}
    assert {"revenue", "aov", "gmv", "headcount"} <= names
    assert {e[0] for e in entities} == {"orders", "customers"}
    assert joins[0] == ("orders_customers", "many_to_one")


async def test_outbox_dispatcher_publishes_after_commit(client, container):
    from app.store.sql import OutboxDispatcher

    await create_published_model(client)
    dispatcher = OutboxDispatcher(container.extras["session_factory"],
                                  container.bus)
    published = await dispatcher.run_once()
    assert published >= 2  # model.created + model.version_published + ...
    types = {e["event_type"] for _, e in container.bus.published}
    assert {"model.created", "model.version_submitted",
            "model.version_published"} <= types
    # second run drains nothing new
    assert await dispatcher.run_once() == 0


async def test_idempotency_replay_persisted(client):
    headers = {**auth(), "Idempotency-Key": "int-key-1"}
    resp1 = await client.post(
        "/api/v1/models", json={"workspace_id": WORKSPACE, "name": "idem"},
        headers=headers)
    resp2 = await client.post(
        "/api/v1/models", json={"workspace_id": WORKSPACE, "name": "idem"},
        headers=headers)
    assert resp2.headers.get("idempotency-replayed") == "true"
    assert resp1.json()["data"]["id"] == resp2.json()["data"]["id"]


async def test_pgvector_semantic_search(client):
    vq = await create_vq(client)
    await approve_vq(client, vq["id"])
    await create_vq(client, nl_text="unrelated churn dashboards",
                    sql_text="SELECT 1 FROM {{dataset('Customers')}}")
    resp = await client.get(
        "/api/v1/verified-queries/search",
        params={"q": "monthly revenue by region", "workspace_id": WORKSPACE},
        headers=auth())
    assert resp.status_code == 200, resp.text
    results = resp.json()["data"]
    assert [r["id"] for r in results] == [vq["id"]]
    assert results[0]["score"] > 0.3


async def test_version_immutability_enforced(client):
    model = await create_published_model(client)
    resp = await client.patch(f"/api/v1/models/{model['id']}/versions/1",
                              json={"definition": {}}, headers=auth())
    assert resp.status_code == 409
