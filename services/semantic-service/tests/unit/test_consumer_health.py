"""Event consumers: schema-change health (SEM-FR-008, AC-7), verified-query
re-validation (SEM-FR-043, AC-11), chart reverse index, workspace deletion,
consumer idempotency (MASTER-FR-032)."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.conftest import (
    ORDERS_URN,
    TENANT_A,
    WORKSPACE,
    auth,
    create_published_model,
)
from tests.unit.test_verified_queries import approve_vq, create_vq


def envelope(event_type: str, payload: dict, *, tenant=TENANT_A,
             event_id: str = None, resource_urn: str = "") -> dict:
    import uuid
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "tenant_id": tenant,
        "actor": {"type": "service", "id": "dataset-service"},
        "via_agent": None,
        "resource_urn": resource_urn,
        "occurred_at": datetime.now(UTC).isoformat(),
        "trace_id": "trace-test",
        "payload": payload,
    }


COMPILE = {"model": "sales", "workspace_id": WORKSPACE, "dialect": "trino"}


async def test_ac7_schema_change_breaks_measure_but_not_others(client, container):
    await create_published_model(client)

    await container.bus.publish("dataset.events.v1", envelope(
        "dataset.schema_changed",
        {"dataset_urn": ORDERS_URN, "removed_columns": ["order_total"]}))

    # health lists the broken measures
    resp = await client.get("/api/v1/models?filter[workspace_id]=" + WORKSPACE,
                            headers=auth())
    health = resp.json()["data"][0]["health"]
    assert health["status"] == "broken"
    broken_names = {r["name"] for r in health["broken_refs"]}
    assert {"revenue", "avg_order_value", "completed_revenue"} <= broken_names
    assert "order_count" not in broken_names

    # compile of a broken measure -> 409 MODEL_UNHEALTHY
    resp = await client.post("/api/v1/compile",
                             json={**COMPILE, "metrics": ["revenue"]}, headers=auth())
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "MODEL_UNHEALTHY"

    # unaffected metrics still compile
    resp = await client.post(
        "/api/v1/compile",
        json={**COMPILE, "metrics": ["order_count"], "dimensions": ["region"]},
        headers=auth())
    assert resp.status_code == 200

    # model.health_changed emitted
    events = container.memory_state.events_of_type("model.health_changed")
    assert events and {r["name"] for r in events[0]["payload"]["broken_refs"]} \
        >= {"revenue"}


async def test_health_recovers_when_column_returns(client, container):
    await create_published_model(client)
    await container.bus.publish("dataset.events.v1", envelope(
        "dataset.schema_changed",
        {"dataset_urn": ORDERS_URN, "removed_columns": ["order_total"]}))
    await container.bus.publish("dataset.events.v1", envelope(
        "dataset.schema_changed",
        {"dataset_urn": ORDERS_URN, "removed_columns": ["some_other_col"]}))
    resp = await client.get(f"/api/v1/models?filter[workspace_id]={WORKSPACE}",
                            headers=auth())
    assert resp.json()["data"][0]["health"]["status"] == "ok"


async def test_ac11_approved_query_moves_to_pending_review_on_schema_break(
        client, container):
    vq = await create_vq(client)
    await approve_vq(client, vq["id"])
    await container.bus.publish("dataset.events.v1", envelope(
        "dataset.schema_changed",
        {"dataset_urn": ORDERS_URN, "removed_columns": ["order_total"]}))
    resp = await client.get(f"/api/v1/verified-queries/{vq['id']}", headers=auth())
    data = resp.json()["data"]
    assert data["status"] == "pending_review"
    assert "order_total" in data["health_note"]


async def test_consumer_dedup_is_idempotent(client, container):
    await create_published_model(client)
    ev = envelope("dataset.schema_changed",
                  {"dataset_urn": ORDERS_URN, "removed_columns": ["order_total"]},
                  event_id="00000000-0000-4000-8000-000000000001")
    await container.bus.publish("dataset.events.v1", ev)
    await container.bus.publish("dataset.events.v1", ev)  # replay
    events = container.memory_state.events_of_type("model.health_changed")
    assert len(events) == 1


async def test_dataset_deleted_breaks_all_bound_objects(client, container):
    await create_published_model(client)
    await container.bus.publish("dataset.events.v1", envelope(
        "dataset.deleted", {"dataset_urn": ORDERS_URN}))
    resp = await client.get(f"/api/v1/models?filter[workspace_id]={WORKSPACE}",
                            headers=auth())
    health = resp.json()["data"][0]["health"]
    assert health["status"] == "broken"
    assert {r["name"] for r in health["broken_refs"]} >= {"orders", "revenue", "region"}


async def test_dataset_restored_heals_dataset_deleted_break(client, container):
    """Regression: dataset-service emits dataset.restored on POST
    /datasets/{id}/restore, but this consumer never had a handler for it, so
    archiving a dataset bound to a semantic model broke every measure/
    dimension PERMANENTLY -- restoring the dataset never healed the model
    (found live: chart-service compile 409 MODEL_UNHEALTHY on a dashboard
    whose dataset had been archived-then-restored during unrelated testing)."""
    await create_published_model(client)
    await container.bus.publish("dataset.events.v1", envelope(
        "dataset.deleted", {"dataset_urn": ORDERS_URN}))
    resp = await client.get(f"/api/v1/models?filter[workspace_id]={WORKSPACE}",
                            headers=auth())
    assert resp.json()["data"][0]["health"]["status"] == "broken"

    await container.bus.publish("dataset.events.v1", envelope(
        "dataset.restored", {"dataset_urn": ORDERS_URN}))
    resp = await client.get(f"/api/v1/models?filter[workspace_id]={WORKSPACE}",
                            headers=auth())
    health = resp.json()["data"][0]["health"]
    assert health["status"] == "ok"
    assert health["broken_refs"] == []

    # a previously-broken metric compiles again
    resp = await client.post("/api/v1/compile",
                             json={**COMPILE, "metrics": ["revenue"]}, headers=auth())
    assert resp.status_code == 200

    events = container.memory_state.events_of_type("model.health_changed")
    assert events[-1]["payload"]["broken_refs"] == []


async def test_dataset_restored_leaves_unrelated_break_alone(client, container):
    """A model broken by a REAL schema_changed (not dataset.deleted) must not
    be silently healed just because some other dataset it also binds got
    restored -- that would mask an actual, unresolved problem."""
    await create_published_model(client)
    await container.bus.publish("dataset.events.v1", envelope(
        "dataset.schema_changed",
        {"dataset_urn": ORDERS_URN, "removed_columns": ["order_total"]}))
    resp = await client.get(f"/api/v1/models?filter[workspace_id]={WORKSPACE}",
                            headers=auth())
    assert resp.json()["data"][0]["health"]["status"] == "broken"

    await container.bus.publish("dataset.events.v1", envelope(
        "dataset.restored", {"dataset_urn": ORDERS_URN}))
    resp = await client.get(f"/api/v1/models?filter[workspace_id]={WORKSPACE}",
                            headers=auth())
    # still broken: the schema_changed break has reason != "dataset deleted"
    assert resp.json()["data"][0]["health"]["status"] == "broken"


async def test_chart_reverse_index_feeds_deprecation_impact(client, container):
    """chart.events.v1 -> chart_refs; measure.deprecated lists impacted charts."""
    await container.bus.publish("chart.events.v1", envelope(
        "chart.created", {"measures": ["gmv"], "model": "sales"},
        resource_urn=f"wr:{TENANT_A}:chart:chart/018f-chart-1"))
    await create_published_model(client)  # publish emits measure.deprecated for gmv
    events = container.memory_state.events_of_type("measure.deprecated")
    assert events
    assert events[0]["payload"]["measure"] == "gmv"
    assert events[0]["payload"]["successor"] == "revenue"
    assert events[0]["payload"]["impacted_charts"] == \
        [f"wr:{TENANT_A}:chart:chart/018f-chart-1"]


async def test_workspace_deleted_soft_deletes_models(client, container):
    model = await create_published_model(client)
    await container.bus.publish("rbac.events.v1", envelope(
        "workspace.deleted", {"workspace_id": WORKSPACE}))
    resp = await client.get(f"/api/v1/models/{model['id']}", headers=auth())
    assert resp.status_code == 404


async def test_verified_query_revalidated_on_model_publish_removal(client, container):
    """SEM-FR-043: publish that removes an object re-checks approved queries."""
    model = await create_published_model(client)
    vq = await create_vq(client, model="sales",
                         sql_text="SELECT region, sum(order_total) AS revenue "
                                  "FROM {{dataset('Orders')}} GROUP BY 1")
    await approve_vq(client, vq["id"])

    from tests.conftest import SALES_DEFINITION, publish_model
    resp = await client.post(f"/api/v1/models/{model['id']}/versions", headers=auth())
    assert resp.status_code == 201
    slim = {**SALES_DEFINITION,
            "measures": [m for m in SALES_DEFINITION["measures"]
                         if m["name"] != "revenue"],
            "dimensions": SALES_DEFINITION["dimensions"]}
    # drop derived aov too (it references revenue)
    slim["measures"] = [m for m in slim["measures"] if m["name"] != "aov"]
    resp = await client.patch(f"/api/v1/models/{model['id']}/versions/2",
                              json={"definition": slim}, headers=auth())
    assert resp.status_code == 200, resp.text
    await publish_model(client, model["id"], version_no=2)

    resp = await client.get(f"/api/v1/verified-queries/{vq['id']}", headers=auth())
    data = resp.json()["data"]
    assert data["status"] == "pending_review"
    assert "revenue" in data["health_note"]
