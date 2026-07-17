"""Bootstrap from V1 chart configs + saved queries (SEM-FR-060..062, AC-10,
BR-12): derivation, dedup by expression, conflicts, idempotence, origins."""

from __future__ import annotations

from tests.conftest import CUSTOMERS_URN, ORDERS_URN, WORKSPACE, auth, create_model


def chart(idx: int, chart_type: str, *, x="region", y="order_total",
          dataseries=None, meta=None, dataset_urn=ORDERS_URN) -> dict:
    return {
        "id": f"018e-chart-{idx}", "chart_type": chart_type,
        "dataset_urn": dataset_urn,
        "config": {"x": x, "y": y, "dataseries": dataseries},
        "meta": meta if meta is not None
        else {"aggregate": {"type": "sum", "checked": True}},
    }


def twelve_charts() -> list[dict]:
    """Pie/bar/line mix with ySeries.aggregateType per AC-10, plus edge shapes."""
    return [
        chart(1, "pie_chart", meta={"aggregate": {"type": "sum", "checked": True}}),
        chart(2, "pie_chart", y="discount",
              meta={"aggregate": {"type": "avg", "checked": True}}),
        chart(3, "vertical_bar_chart", y=["order_total", "discount"],
              meta={"aggregate": {"checked": True},
                    "ySeries": {"orderTotal": {"aggregateType": "sum"},
                                "discount": {"aggregateType": "avg"}}}),
        chart(4, "vertical_stackedbar_chart", x="status", y=["order_total"],
              meta={"aggregate": {"checked": True},
                    "ySeries": {"orderTotal": {"aggregateType": "sum"}}}),
        chart(5, "line_chart", x="order_date", y=["order_total"],
              dataseries="region",
              meta={"aggregate": {"checked": True},
                    "ySeries": {"orderTotal": {"aggregateType": "sum"}}}),
        chart(6, "line_chart", x="order_date", y=["gmv_amount"],
              meta={"aggregate": {"checked": True},
                    "ySeries": {"gmvAmount": {"aggregateType": "max"}}}),
        chart(7, "scatter_plot", x="order_total", y="discount", meta={}),  # raw
        chart(8, "scatter_plot", x="region", y=["order_total"],
              meta={"aggregate": {"checked": True},
                    "ySeries": {"orderTotal": {"aggregateType": "min"}}}),
        chart(9, "sankey_chart", meta={}),  # passthrough
        chart(10, "vertical_bar_chart", x="tier", y=["id"],
              dataset_urn=CUSTOMERS_URN,
              meta={"aggregate": {"checked": True},
                    "ySeries": {"id": {"aggregateType": "count"}}}),
        chart(11, "pie_chart", y="order_total",  # duplicate of 1 -> dedup
              meta={"aggregate": {"type": "sum", "checked": True}}),
        chart(12, "vertical_bar_chart", x="region", y=["no_such_col"],
              meta={"aggregate": {"checked": True}}),  # skipped: not in schema
    ]


def eight_saved_queries() -> list[dict]:
    q = [
        ("SELECT region, sum(order_total) FROM {{dataset('Orders')}} GROUP BY region",
         ORDERS_URN),
        ("SELECT status, count(*) FROM {{dataset('Orders')}} GROUP BY status",
         ORDERS_URN),
        ("SELECT region, count(distinct customer_id) FROM {{dataset('Orders')}} "
         "GROUP BY region", ORDERS_URN),
        ("SELECT sum(order_total), avg(discount) FROM {{dataset('Orders')}}",
         ORDERS_URN),
        ("SELECT tier, count(id) FROM {{dataset('Customers')}} GROUP BY tier",
         CUSTOMERS_URN),
        ("SELECT region, min(order_total), max(order_total) "
         "FROM {{dataset('Orders')}} GROUP BY region ORDER BY 1", ORDERS_URN),
        ("SELECT * FROM {{dataset('Orders')}} LIMIT 10", ORDERS_URN),  # nothing
        ("SELECT region, sum(order_total) FROM {{dataset('Orders')}} "
         "GROUP BY region", ORDERS_URN),  # duplicate -> dedup
    ]
    return [{"id": f"018e-q-{i}", "sql": sql, "dataset_urn": urn}
            for i, (sql, urn) in enumerate(q, start=1)]


async def run_bootstrap(client, model_id: str) -> dict:
    resp = await client.post(
        f"/api/v1/models/{model_id}/bootstrap",
        json={"sources": {"chart_configs": twelve_charts(),
                          "saved_queries": eight_saved_queries()},
              "workspace": WORKSPACE},
        headers=auth())
    assert resp.status_code == 202, resp.text
    return resp.json()["data"]


async def test_ac10_bootstrap_derives_draft_definition(client):
    model = await create_model(client, name="fresh", definition={})
    data = await run_bootstrap(client, model["id"])
    report = data["report"]

    assert report["status"] == "completed"
    created = report["created"]
    assert created["entities"] == 2  # Orders + Customers
    # dimensions: region, order_date, status (charts) + tier (chart 10) etc.
    assert created["dimensions"] >= 4
    assert created["measures"] >= 7
    assert "sum_order_total" in created["examples"]

    skipped = {s["source"]: s["reason"] for s in report["skipped"]}
    assert "passthrough chart_type sankey_chart" in skipped["chart/018e-chart-9"]
    assert "raw rows" in skipped["chart/018e-chart-7"]
    assert "not in dataset schema" in skipped["chart/018e-chart-12"]

    # derived draft definition compiles structurally and carries origins
    resp = await client.get(f"/api/v1/models/{model['id']}/versions/1", headers=auth())
    definition = resp.json()["data"]["definition"]
    measures = {m["name"]: m for m in definition["measures"]}
    assert measures["sum_order_total"]["origin"] == "bootstrap"
    assert measures["avg_discount"]["agg"] == "avg"
    assert measures["count_distinct_customer_id"]["agg"] == "count_distinct"
    assert measures["count_all"]["agg"] == "count" and \
        measures["count_all"].get("expr") is None
    dims = {d["name"]: d for d in definition["dimensions"]}
    assert dims["order_date"]["type"] == "time"
    assert dims["order_date"]["time_grains"] == ["day", "week", "month",
                                                 "quarter", "year"]
    assert dims["region"]["type"] == "categorical"

    # operation endpoint replays the report
    resp = await client.get(f"/api/v1/operations/{data['operation_id']}",
                            headers=auth())
    assert resp.json()["data"]["report"]["created"] == created

    # bootstrap.completed event emitted


async def test_ac10_bootstrap_idempotent_rerun_changes_nothing(client):
    model = await create_model(client, name="fresh", definition={})
    first = await run_bootstrap(client, model["id"])
    resp = await client.get(f"/api/v1/models/{model['id']}/versions/1", headers=auth())
    def_after_first = resp.json()["data"]["definition"]

    second = await run_bootstrap(client, model["id"])
    resp = await client.get(f"/api/v1/models/{model['id']}/versions/1", headers=auth())
    def_after_second = resp.json()["data"]["definition"]

    assert def_after_first == def_after_second  # SEM-FR-061
    assert second["report"]["created"]["measures"] == 0
    assert second["report"]["created"]["dimensions"] == 0
    assert second["report"]["created"]["entities"] == 0
    assert first["report"]["created"]["measures"] > 0


async def test_br12_bootstrap_never_overwrites_manual_items(client):
    manual_def = {
        "entities": [{"name": "orders", "dataset_urn": ORDERS_URN,
                      "table": "bronze.t42.ds_orders", "primary_key": ["order_id"],
                      "dataset_version_policy": {"policy": "latest"}}],
        "measures": [{"name": "sum_order_total", "entity": "orders", "agg": "sum",
                      "expr": "discount", "origin": "manual"}],  # unusual expr
    }
    model = await create_model(client, name="manual", definition=manual_def)
    data = await run_bootstrap(client, model["id"])
    conflicts = data["report"]["conflicts"]
    assert any(c["name"] == "sum_order_total" and c["action"] == "kept_existing"
               for c in conflicts)
    resp = await client.get(f"/api/v1/models/{model['id']}/versions/1", headers=auth())
    measures = {m["name"]: m for m in resp.json()["data"]["definition"]["measures"]}
    assert measures["sum_order_total"]["expr"] == "discount"  # untouched


async def test_bootstrap_emits_completed_event(client, container):
    model = await create_model(client, name="fresh", definition={})
    await run_bootstrap(client, model["id"])
    events = container.memory_state.events_of_type("bootstrap.completed")
    assert events and events[0]["payload"]["created_counts"]["entities"] == 2


async def test_bootstrap_rejected_when_version_in_review(client):
    model = await create_model(client, name="fresh", definition={})
    resp = await client.post(f"/api/v1/models/{model['id']}/versions/1/submit",
                             headers=auth())
    assert resp.status_code == 200
    resp = await client.post(
        f"/api/v1/models/{model['id']}/bootstrap", json={"sources": {}},
        headers=auth())
    assert resp.status_code == 409
