"""Model CRUD + versioning + review workflow (SEM-FR-001/006/007, AC-6, BR-2/10)."""

from __future__ import annotations

import asyncio

from tests.conftest import (
    SALES_DEFINITION,
    WORKSPACE,
    auth,
    create_model,
    create_published_model,
    publish_model,
)


async def test_create_and_get_model(client):
    model = await create_model(client)
    assert model["name"] == "sales"
    assert model["draft_version"]["version_no"] == 1
    resp = await client.get(f"/api/v1/models/{model['id']}", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["data"]["published_version_no"] is None


async def test_duplicate_name_conflict(client):
    await create_model(client)
    resp = await client.post(
        "/api/v1/models",
        json={"workspace_id": WORKSPACE, "name": "SALES"},
        headers=auth())
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_list_models_paginated(client):
    for i in range(3):
        await create_model(client, name=f"model_{i}", definition={})
    resp = await client.get("/api/v1/models?limit=2", headers=auth())
    body = resp.json()
    assert len(body["data"]) == 2 and body["page"]["has_more"] is True
    resp2 = await client.get(
        f"/api/v1/models?limit=2&cursor={body['page']['next_cursor']}", headers=auth())
    assert len(resp2.json()["data"]) == 1


async def test_ac6_review_workflow(client, container):
    """AC-6: author cannot approve; steward approval publishes, supersedes the
    prior version, and model.version_published carries the diff."""
    model = await create_model(client)
    model_id = model["id"]

    resp = await client.post(f"/api/v1/models/{model_id}/versions/1/submit",
                             headers=auth(sub="author-1"))
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "in_review"

    # author X approving -> 403
    resp = await client.post(f"/api/v1/models/{model_id}/versions/1/approve",
                             headers=auth(sub="author-1"))
    assert resp.status_code == 403

    # steward Y approves -> published
    resp = await client.post(f"/api/v1/models/{model_id}/versions/1/approve",
                             headers=auth(sub="steward-1"))
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "published"

    events = container.memory_state.events_of_type("model.version_published")
    assert len(events) == 1
    assert "measures" in events[0]["payload"]["diff"]["added"]

    # v2: drop gmv, publish, v1 -> superseded, diff shows removal
    resp = await client.post(f"/api/v1/models/{model_id}/versions", headers=auth())
    assert resp.status_code == 201
    new_def = {**SALES_DEFINITION,
               "measures": [m for m in SALES_DEFINITION["measures"]
                            if m["name"] != "gmv"]}
    resp = await client.patch(f"/api/v1/models/{model_id}/versions/2",
                              json={"definition": new_def}, headers=auth())
    assert resp.status_code == 200
    await publish_model(client, model_id, version_no=2)

    resp = await client.get(f"/api/v1/models/{model_id}/versions/1", headers=auth())
    assert resp.json()["data"]["status"] == "superseded"
    published = container.memory_state.events_of_type("model.version_published")
    assert published[-1]["payload"]["diff"]["removed"]["measures"] == ["gmv"]


async def test_submit_fails_on_broken_binding(client):
    bad = {**SALES_DEFINITION,
           "measures": SALES_DEFINITION["measures"]
           + [{"name": "bogus", "entity": "orders", "agg": "sum",
               "expr": "no_such_column"}]}
    model = await create_model(client, name="badmodel", definition=bad)
    resp = await client.post(f"/api/v1/models/{model['id']}/versions/1/submit",
                             headers=auth())
    assert resp.status_code == 422
    problems = resp.json()["error"]["details"]
    assert any("no_such_column" in p["problem"] for p in problems)


async def test_reject_requires_note_then_revise(client):
    model = await create_model(client)
    model_id = model["id"]
    await client.post(f"/api/v1/models/{model_id}/versions/1/submit",
                      headers=auth(sub="author-1"))
    resp = await client.post(f"/api/v1/models/{model_id}/versions/1/reject",
                             json={}, headers=auth(sub="steward-1"))
    assert resp.status_code == 422
    resp = await client.post(f"/api/v1/models/{model_id}/versions/1/reject",
                             json={"note": "needs work"}, headers=auth(sub="steward-1"))
    assert resp.status_code == 200
    # rejected -> draft on revise, same version_no (§4.2)
    resp = await client.patch(f"/api/v1/models/{model_id}/versions/1",
                              json={"definition": SALES_DEFINITION}, headers=auth())
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "draft"


async def test_no_content_edits_outside_draft(client):
    model = await create_published_model(client)
    resp = await client.patch(f"/api/v1/models/{model['id']}/versions/1",
                              json={"definition": {}}, headers=auth())
    assert resp.status_code == 409


async def test_only_one_open_version(client):
    model = await create_model(client)
    resp = await client.post(f"/api/v1/models/{model['id']}/versions", headers=auth())
    assert resp.status_code == 409


async def test_definition_endpoint_etag_and_versions(client):
    model = await create_published_model(client)
    resp = await client.get(f"/api/v1/models/{model['id']}/definition", headers=auth())
    assert resp.status_code == 200
    assert resp.headers.get("etag")
    assert resp.json()["data"]["version_no"] == 1
    resp = await client.get(
        f"/api/v1/models/{model['id']}/definition?version=1", headers=auth())
    assert resp.status_code == 200


async def test_expression_not_allowed_at_save(client):
    bad = {**SALES_DEFINITION,
           "measures": SALES_DEFINITION["measures"]
           + [{"name": "evil", "entity": "orders", "agg": "sum",
               "expr": "order_total; DROP TABLE x"}]}
    resp = await client.post(
        "/api/v1/models",
        json={"workspace_id": WORKSPACE, "name": "evilmodel", "definition": bad},
        headers=auth())
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "EXPRESSION_NOT_ALLOWED"


async def test_br10_concurrent_approvals_cannot_both_publish(client, container):
    model = await create_model(client)
    model_id = model["id"]
    await client.post(f"/api/v1/models/{model_id}/versions/1/submit",
                      headers=auth(sub="author-1"))

    results = await asyncio.gather(
        client.post(f"/api/v1/models/{model_id}/versions/1/approve",
                    headers=auth(sub="steward-1")),
        client.post(f"/api/v1/models/{model_id}/versions/1/approve",
                    headers=auth(sub="steward-2")),
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409]  # exactly one wins the advisory lock


async def test_idempotency_key_replays_create(client):
    headers = {**auth(), "Idempotency-Key": "key-1"}
    resp1 = await client.post(
        "/api/v1/models", json={"workspace_id": WORKSPACE, "name": "idem"},
        headers=headers)
    assert resp1.status_code == 201
    resp2 = await client.post(
        "/api/v1/models", json={"workspace_id": WORKSPACE, "name": "idem"},
        headers=headers)
    assert resp2.status_code == 201
    assert resp2.headers.get("idempotency-replayed") == "true"
    assert resp2.json()["data"]["id"] == resp1.json()["data"]["id"]


async def test_soft_delete(client):
    model = await create_model(client)
    resp = await client.delete(f"/api/v1/models/{model['id']}", headers=auth())
    assert resp.status_code == 204
    resp = await client.get(f"/api/v1/models/{model['id']}", headers=auth())
    assert resp.status_code == 404
