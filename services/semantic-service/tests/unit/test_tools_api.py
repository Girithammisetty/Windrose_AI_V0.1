"""MCP-facing tools (SEM-FR-080/081) + THE normative dual-consumer contract
test: byte-identical SQL across the chart path and the metric path (AC-5)."""

from __future__ import annotations

from tests.conftest import WORKSPACE, auth, create_published_model


async def test_tool_catalog_exposes_json_schemas(client):
    resp = await client.get("/api/v1/tools", headers=auth())
    tools = {t["name"]: t for t in resp.json()["data"]}
    assert set(tools) == {"get_metrics", "get_dimensions", "compile_metric_sql",
                          "search_verified_queries"}
    for tool in tools.values():
        assert tool["input_schema"]["type"] == "object"
        assert tool["output_schema"]["type"] == "object"
        assert tool["version"]


async def test_get_metrics_shape(client, container):
    await create_published_model(client)
    resp = await client.post("/api/v1/tools/get_metrics",
                             json={"model": "sales", "workspace_id": WORKSPACE},
                             headers=auth())
    metrics = {m["name"]: m for m in resp.json()["data"]["metrics"]}
    assert metrics["revenue"]["agg"] == "sum"
    assert metrics["revenue"]["entity"] == "orders"
    assert metrics["revenue"]["synonyms"] == ["sales"]
    assert metrics["revenue"]["deprecated"] is False
    assert metrics["gmv"]["deprecated"] is True
    assert metrics["gmv"]["successor"] == "revenue"
    assert metrics["revenue"]["model_version"] == "sales@v1"
    audits = container.memory_state.events_of_type("ai.tool_invoked.v1")
    assert audits[-1]["payload"]["tool"] == "get_metrics"


async def test_get_dimensions_with_sample_values(client):
    await create_published_model(client)
    resp = await client.post("/api/v1/tools/get_dimensions",
                             json={"model": "sales", "workspace_id": WORKSPACE},
                             headers=auth())
    dims = {d["name"]: d for d in resp.json()["data"]["dimensions"]}
    assert dims["order_month"]["time_grains"] == ["day", "week", "month",
                                                  "quarter", "year"]
    assert dims["region"]["sample_values"] == ["EMEA", "AMER", "APAC"]
    assert dims["customer_tier"]["sample_values"] == ["gold", "silver", "bronze"]


async def test_get_dimensions_scoped_by_metric(client):
    await create_published_model(client)
    resp = await client.post("/api/v1/tools/get_dimensions",
                             json={"metric": "revenue", "workspace_id": WORKSPACE},
                             headers=auth())
    names = {d["name"] for d in resp.json()["data"]["dimensions"]}
    assert "region" in names
    resp = await client.post("/api/v1/tools/get_dimensions",
                             json={"metric": "nope", "workspace_id": WORKSPACE},
                             headers=auth())
    assert resp.status_code == 422


async def test_compile_metric_sql_validates_and_clamps(client, container):
    await create_published_model(client)
    resp = await client.post(
        "/api/v1/tools/compile_metric_sql",
        json={"model": "sales", "workspace_id": WORKSPACE, "dialect": "trino",
              "metrics": ["revenue"], "dimensions": ["region"]},
        headers=auth(typ="agent_obo", agent_id="analytics-agent",
                     agent_version="1", obo_sub="user-9"))
    data = resp.json()["data"]
    assert data["validation"]["verdict"] == "ok"  # validate=true always (FR-080)
    assert "LIMIT 10000" in data["sql"]  # agent ceiling applied
    assert any(w.startswith("LIMIT_CLAMPED") for w in data["warnings"])


async def test_ac5_contract_byte_identical_sql_across_consumers(client, container):
    """SEM-FR-081 NORMATIVE: an identical compile request through
    (1) POST /compile (chart-service's building block), (2) POST /compile/chart,
    and (3) the MCP tool compile_metric_sql yields byte-identical SQL and the
    same model_version."""
    await create_published_model(client)

    compile_body = {
        "model": "sales", "workspace_id": WORKSPACE, "dialect": "trino",
        "metrics": ["revenue", "avg_order_value"],
        "dimensions": ["region"],
        "filters": [{"dimension": "status", "op": "=", "values": ["completed"]}],
        "limit": 1000,
    }
    chart_body = {
        "model": "sales", "workspace_id": WORKSPACE, "dialect": "trino",
        "chart_type": "vertical_bar_chart", "x": "region",
        "y": [{"measure": "revenue"}, {"measure": "avg_order_value"}],
        "filters": [{"dimension": "status", "op": "=", "values": ["completed"]}],
        "limit": 1000,
    }

    api = await client.post("/api/v1/compile", json=compile_body, headers=auth())
    chart = await client.post("/api/v1/compile/chart", json=chart_body, headers=auth())
    tool = await client.post(
        "/api/v1/tools/compile_metric_sql", json=compile_body,
        headers=auth(typ="agent_obo", agent_id="analytics-agent", agent_version="1",
                     obo_sub="user-9"))
    assert api.status_code == chart.status_code == tool.status_code == 200

    sql_api = api.json()["data"]["sql"]
    sql_chart = chart.json()["data"]["sql"]
    sql_tool = tool.json()["data"]["sql"]
    assert sql_api == sql_chart == sql_tool  # byte-identical
    assert (api.json()["data"]["provenance"]["model_version"]
            == chart.json()["data"]["provenance"]["model_version"]
            == tool.json()["data"]["provenance"]["model_version"]
            == "sales@v1")
    assert (api.json()["data"]["params"] == chart.json()["data"]["params"]
            == tool.json()["data"]["params"])
    # caller classes recorded distinctly in compile_log
    callers = {e.caller_class for e in container.memory_state.compile_log}
    assert {"api", "chart", "agent_tool"} <= callers


async def test_unknown_tool_404(client):
    resp = await client.post("/api/v1/tools/get_metrics", json={},
                             headers=auth())
    assert resp.status_code == 200  # known tool with empty scope
    resp = await client.get("/api/v1/tools/nope", headers=auth())
    assert resp.status_code in (404, 405)
