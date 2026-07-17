"""Connection API behaviour (ING-FR-001..006, MASTER API standards)."""

from __future__ import annotations

from tests.util import TENANT_A, VALID_PG_CONNECTION, create_connection, outbox_events


async def test_create_returns_envelope_and_never_secrets(client, auth_a, container) -> None:
    data = await create_connection(client, auth_a)
    assert data["secret_set"] is True
    assert data["secrets"] == {"password": "•••"}
    assert data["last_test_status"] == "ok"
    assert "s3cr3t" not in str(data)
    # secret lives only in the secrets store (BR-1)
    assert "s3cr3t-pw" in container.secrets.dump_all_values()


async def test_read_masks_secrets(client, auth_a) -> None:
    created = await create_connection(client, auth_a)
    resp = await client.get(f"/api/v1/connections/{created['id']}", headers=auth_a)
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["secrets"] == {"password": "•••"}
    assert "s3cr3t" not in resp.text


async def test_trace_id_header_present(client, auth_a) -> None:
    resp = await client.get("/api/v1/connections", headers=auth_a)
    assert resp.status_code == 200
    assert resp.headers.get("X-Trace-Id")


async def test_name_unique_per_workspace_case_insensitive(client, auth_a) -> None:
    await create_connection(client, auth_a, name="Warehouse")
    resp = await client.post(
        "/api/v1/connections", json={**VALID_PG_CONNECTION, "name": "warehouse"}, headers=auth_a
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_validation_failed_has_per_field_details(client, auth_a) -> None:
    payload = {
        **VALID_PG_CONNECTION,
        "config": {**VALID_PG_CONNECTION["config"], "bogus_field": 1},
    }
    resp = await client.post("/api/v1/connections", json=payload, headers=auth_a)
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert any("bogus_field" in d["field"] for d in error["details"])


async def test_unreachable_host_fails_test_and_persists_nothing(client, auth_a) -> None:
    payload = {
        **VALID_PG_CONNECTION,
        "config": {**VALID_PG_CONNECTION["config"], "host": "unreachable.acme.internal"},
    }
    resp = await client.post("/api/v1/connections", json=payload, headers=auth_a)
    assert resp.status_code == 424
    error = resp.json()["error"]
    assert error["code"] == "CONNECTION_TEST_FAILED"
    assert error["details"]["error_category"] == "SOURCE_UNREACHABLE"
    listing = await client.get("/api/v1/connections", headers=auth_a)
    assert listing.json()["data"] == []


async def test_skip_test_bypasses_probe(client, auth_a) -> None:
    payload = {
        **VALID_PG_CONNECTION,
        "skip_test": True,
        "config": {**VALID_PG_CONNECTION["config"], "host": "unreachable.acme.internal"},
    }
    resp = await client.post("/api/v1/connections", json=payload, headers=auth_a)
    assert resp.status_code == 201
    assert resp.json()["data"]["last_test_status"] is None


async def test_test_endpoints(client, auth_a) -> None:
    created = await create_connection(client, auth_a)
    resp = await client.post(f"/api/v1/connections/{created['id']}/test", headers=auth_a)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"
    assert "latency_ms" in resp.json()["data"]

    adhoc = await client.post(
        "/api/v1/connections:test",
        json={
            "connector_type": "postgres",
            "config": {"host": "badauth.db", "database": "d", "username": "u"},
            "secrets": {"password": "nope"},
        },
        headers=auth_a,
    )
    assert adhoc.status_code == 200
    assert adhoc.json()["data"]["status"] == "failed"
    assert adhoc.json()["data"]["error_category"] == "AUTH_FAILED"


async def test_preview_returns_rows_without_persisting(client, auth_a) -> None:
    created = await create_connection(client, auth_a)
    resp = await client.post(
        f"/api/v1/connections/{created['id']}/preview",
        json={"table": "public.orders", "limit": 2},
        headers=auth_a,
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert len(body["rows"]) == 2
    assert body["columns"]


async def test_secret_rotation_via_patch(client, auth_a, container) -> None:
    created = await create_connection(client, auth_a)
    resp = await client.patch(
        f"/api/v1/connections/{created['id']}",
        json={"secrets": {"password": "new-pw"}},
        headers=auth_a,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["secrets"] == {"password": "•••"}
    values = container.secrets.dump_all_values()
    assert "new-pw" in values


async def test_list_filters_and_cursor_pagination(client, auth_a) -> None:
    for i in range(5):
        await create_connection(client, auth_a, name=f"pg-{i}")
    await create_connection(
        client,
        auth_a,
        name="bucket",
        connector_type="s3",
        config={"bucket": "b"},
        secrets={},
    )
    resp = await client.get(
        "/api/v1/connections", params={"filter[connector_type]": "s3"}, headers=auth_a
    )
    assert [c["name"] for c in resp.json()["data"]] == ["bucket"]

    page1 = await client.get("/api/v1/connections", params={"limit": 4}, headers=auth_a)
    body1 = page1.json()
    assert len(body1["data"]) == 4 and body1["page"]["has_more"] is True
    page2 = await client.get(
        "/api/v1/connections",
        params={"limit": 4, "cursor": body1["page"]["next_cursor"]},
        headers=auth_a,
    )
    body2 = page2.json()
    assert len(body2["data"]) == 2 and body2["page"]["has_more"] is False
    ids = {c["id"] for c in body1["data"]} | {c["id"] for c in body2["data"]}
    assert len(ids) == 6


async def test_bad_cursor_rejected(client, auth_a) -> None:
    resp = await client.get("/api/v1/connections", params={"cursor": "@@@"}, headers=auth_a)
    assert resp.status_code == 422


async def test_delete_soft_deletes_and_schedules_vault_destroy(client, auth_a, container) -> None:
    created = await create_connection(client, auth_a)
    resp = await client.delete(f"/api/v1/connections/{created['id']}", headers=auth_a)
    assert resp.status_code == 204
    resp = await client.get(f"/api/v1/connections/{created['id']}", headers=auth_a)
    assert resp.status_code == 404
    assert any(
        created["id"] in path for path in container.secrets.scheduled_destroys
    )  # 7-day grace queued


async def test_missing_or_bad_token_is_401(client) -> None:
    resp = await client.get("/api/v1/connections")
    assert resp.status_code == 401
    resp = await client.get("/api/v1/connections", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


async def test_mutations_emit_events_via_outbox(client, auth_a, container) -> None:
    created = await create_connection(client, auth_a)
    await client.patch(
        f"/api/v1/connections/{created['id']}", json={"tags": ["gold"]}, headers=auth_a
    )
    await client.delete(f"/api/v1/connections/{created['id']}", headers=auth_a)
    types = [e["event_type"] for e in await outbox_events(container, TENANT_A)]
    assert "connection.created" in types
    assert "connection.updated" in types
    assert "connection.deleted" in types


async def test_connector_type_catalog_endpoint(client, auth_a) -> None:
    resp = await client.get("/api/v1/connector-types", headers=auth_a)
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert len(body) == 19
    resp = await client.get("/api/v1/connector-types/postgres", headers=auth_a)
    assert resp.json()["data"]["config_schema"]["additionalProperties"] is False
    resp = await client.get("/api/v1/connector-types/mongodb", headers=auth_a)
    assert resp.status_code == 404
