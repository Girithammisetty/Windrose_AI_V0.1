"""Compile API behaviors over HTTP (SEM-FR-020/024/025, BR-2, AC-1/AC-9)."""

from __future__ import annotations

from tests.conftest import (
    SALES_DEFINITION,
    TENANT_A,
    WORKSPACE,
    auth,
    create_model,
    create_published_model,
)

COMPILE = {
    "model": "sales", "workspace_id": WORKSPACE, "dialect": "trino",
    "metrics": ["revenue"], "dimensions": ["region"],
}


async def test_ac1_compile_shape(client):
    await create_published_model(client)
    resp = await client.post("/api/v1/compile", json=COMPILE, headers=auth())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert 'sum("o"."order_total")' in data["sql"]
    assert "GROUP BY 1" in data["sql"]
    assert data["params"] == []
    assert data["provenance"]["model_version"] == "sales@v1"
    assert data["output_schema"][-1] == {"name": "revenue", "type": "decimal",
                                         "role": "measure"}


async def test_model_not_published_409(client):
    model = await create_model(client)
    resp = await client.post("/api/v1/compile",
                             json={**COMPILE, "model": model["id"]}, headers=auth())
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "MODEL_NOT_PUBLISHED"


async def test_br2_draft_compile_requires_header_and_write_scope(client):
    model = await create_model(client)
    # draft compiles via X-Draft-Version with semantic.model.update
    resp = await client.post(
        "/api/v1/compile", json={**COMPILE, "model": model["id"]},
        headers={**auth(), "X-Draft-Version": "1"})
    assert resp.status_code == 200
    # without write scope -> 403
    resp = await client.post(
        "/api/v1/compile", json={**COMPILE, "model": model["id"]},
        headers={**auth(scopes=["semantic.compile.execute"]),
                 "X-Draft-Version": "1"})
    assert resp.status_code == 403


async def test_compile_by_model_name_requires_workspace(client):
    await create_published_model(client)
    body = dict(COMPILE)
    body.pop("workspace_id")
    resp = await client.post("/api/v1/compile", json=body, headers=auth())
    assert resp.status_code == 422


async def test_validate_true_runs_dry_run(client, container):
    await create_published_model(client)
    resp = await client.post("/api/v1/compile?validate=true", json=COMPILE,
                             headers=auth())
    data = resp.json()["data"]
    assert data["validation"]["verdict"] == "ok"
    assert container.query_client.calls[-1]["sql"] == data["sql"]
    # dry-run is forwarded under the caller's own JWT — query-service has no
    # internal/SPIFFE route (see HttpQueryServiceClient).
    assert container.query_client.calls[-1]["token"]


async def test_validate_true_without_bearer_token_degrades_gracefully(
        client, container):
    """A validate=true compile can never reach this endpoint without a bearer
    token (AuthMiddleware requires one for /api/v1/*) — but CompileService
    still degrades rather than forwarding an empty token, matching the UI's
    'show the SQL, mark cost unavailable' behavior when dry-run can't run."""
    from app.domain.services import CallCtx

    await create_published_model(client)
    result = await container.compile_service.compile(
        CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": "u1"}),
        COMPILE, validate=True, token=None)
    assert result["validation"]["verdict"] == "unavailable"
    assert "sql" in result


async def test_compile_cache_hit_is_byte_identical(client, container):
    await create_published_model(client)
    first = await client.post("/api/v1/compile", json=COMPILE, headers=auth())
    second = await client.post("/api/v1/compile", json=COMPILE, headers=auth())
    assert first.json()["data"]["sql"] == second.json()["data"]["sql"]
    assert len(container.memory_state.compile_log) == 2  # every compile logged


async def test_deprecated_measure_warns(client):
    await create_published_model(client)
    resp = await client.post(
        "/api/v1/compile",
        json={**COMPILE, "metrics": ["gmv"]}, headers=auth())
    assert resp.status_code == 200
    warnings = resp.json()["data"]["warnings"]
    assert any(w.startswith("DEPRECATED: measure gmv") for w in warnings)
    assert "revenue" in warnings[0]  # successor named


async def test_ac9_ambiguous_join_path(client):
    defn = {
        **SALES_DEFINITION,
        "join_paths": SALES_DEFINITION["join_paths"] + [
            {"name": "orders_customers_alt", "from_entity": "orders",
             "to_entity": "customers", "join_type": "inner",
             "on": [{"from_column": "customer_id", "to_column": "id"}],
             "cardinality": "many_to_one"},
        ],
    }
    await create_published_model(client, name="twopaths", definition=defn)
    body = {"model": "twopaths", "workspace_id": WORKSPACE, "dialect": "trino",
            "metrics": ["revenue"], "dimensions": ["customer_tier"]}
    resp = await client.post("/api/v1/compile", json=body, headers=auth())
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "AMBIGUOUS_JOIN_PATH"
    candidates = err["details"][0]["candidates"]
    assert sorted(c[0] for c in candidates) == ["orders_customers",
                                                "orders_customers_alt"]
    # pinning resolves it
    resp = await client.post(
        "/api/v1/compile", json={**body, "join_paths": ["orders_customers"]},
        headers=auth())
    assert resp.status_code == 200
    assert "LEFT JOIN" in resp.json()["data"]["sql"]


async def test_unknown_grain(client):
    await create_published_model(client)
    resp = await client.post(
        "/api/v1/compile",
        json={**COMPILE, "dimensions": [{"name": "order_date", "grain": "quarter"}]},
        headers=auth())
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNKNOWN_GRAIN"


async def test_compile_chart_passthrough_and_aggregate(client):
    """AC-13: sankey -> passthrough:true, no SQL."""
    await create_published_model(client)
    resp = await client.post(
        "/api/v1/compile/chart",
        json={"model": "sales", "workspace_id": WORKSPACE, "chart_type": "sankey_chart"},
        headers=auth())
    data = resp.json()["data"]
    assert data["passthrough"] is True and data["sql"] is None

    resp = await client.post(
        "/api/v1/compile/chart",
        json={"model": "sales", "workspace_id": WORKSPACE, "dialect": "trino",
              "chart_type": "pie_chart", "x": "region", "y": [{"measure": "revenue"}],
              "meta": {"aggregate": {"type": "sum", "checked": True}}},
        headers=auth())
    data = resp.json()["data"]
    assert data["passthrough"] is False
    assert 'sum("o"."order_total") AS "revenue"' in data["sql"]
