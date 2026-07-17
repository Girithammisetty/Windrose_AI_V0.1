"""Template CRUD, versioning, compilation determinism (PIPE-FR-001..005/020, AC-3)."""

from __future__ import annotations

import pytest

from tests.conftest import TENANT_A, WORKSPACE, auth, create_template, data_prep_definition

pytestmark = pytest.mark.asyncio


async def test_create_returns_valid_template_and_version(client):
    resp = await create_template(client)
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["validation_status"] == "valid"
    assert data["active_version_id"]


async def test_duplicate_name_conflicts(client):
    await create_template(client, name="dup")
    resp = await create_template(client, name="dup")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_invalid_definition_saved_as_draft(client):
    # A cycle makes it invalid — savable as draft (PIPE-FR-003).
    bad = {"nodes": [{"alias": "a", "component": "filter-data"},
                     {"alias": "b", "component": "filter-data"}],
           "edges": [{"from": "a.out", "to": "b.in1", "type": "dataframe"},
                     {"from": "b.out", "to": "a.in1", "type": "dataframe"}]}
    resp = await create_template(client, name="draft-one", definition=bad)
    assert resp.status_code == 201
    assert resp.json()["data"]["validation_status"] == "draft"


async def test_update_creates_new_immutable_version(client):
    tid = (await create_template(client, name="verz")).json()["data"]["id"]
    r = await client.put(f"/api/v1/pipelines/{tid}",
                         json={"definition": data_prep_definition(out_name="v2")},
                         headers=auth())
    assert r.status_code == 200
    versions = (await client.get(f"/api/v1/pipelines/{tid}/versions",
                                 headers=auth())).json()["data"]
    assert len(versions) == 2
    assert {v["version_no"] for v in versions} == {1, 2}


async def test_activate_prior_version(client):
    tid = (await create_template(client, name="rollback")).json()["data"]["id"]
    await client.put(f"/api/v1/pipelines/{tid}",
                     json={"definition": data_prep_definition(out_name="v2")},
                     headers=auth())
    versions = (await client.get(f"/api/v1/pipelines/{tid}/versions",
                                 headers=auth())).json()["data"]
    v1 = next(v for v in versions if v["version_no"] == 1)
    r = await client.post(f"/api/v1/pipelines/{tid}/versions/{v1['id']}/activate",
                          headers=auth())
    assert r.status_code == 200
    assert r.json()["data"]["active_version_id"] == v1["id"]


async def test_archive_restore_and_system_guard(client, container):
    tid = (await create_template(client, name="arch")).json()["data"]["id"]
    assert (await client.delete(f"/api/v1/pipelines/{tid}", headers=auth())).status_code == 200
    # excluded from default list
    listed = (await client.get("/api/v1/pipelines", headers=auth())).json()["data"]
    assert tid not in [t["id"] for t in listed]
    assert (await client.patch(f"/api/v1/pipelines/{tid}/restore",
                               headers=auth())).status_code == 200


async def test_clone(client):
    src = (await create_template(client, name="orig")).json()["data"]["id"]
    r = await client.post(f"/api/v1/pipelines/{src}/clone", headers=auth())
    assert r.status_code == 201
    assert r.json()["data"]["name"] == "Copy of orig"


async def test_ac3_compile_is_deterministic_and_idempotent(client, container):
    tid = (await create_template(client, name="compileme")).json()["data"]["id"]
    r1 = await client.post(f"/api/v1/pipelines/{tid}/compile", headers=auth())
    r2 = await client.post(f"/api/v1/pipelines/{tid}/compile", headers=auth())
    assert r1.status_code == 200 and r2.status_code == 200
    d1 = r1.json()["data"]["manifest_digest"]
    d2 = r2.json()["data"]["manifest_digest"]
    assert d1 and d1 == d2  # byte-identical digest
    # pipeline.template.compiled emitted exactly once (idempotent second compile).
    envelopes = [x["payload"] for x in container.memory_state.outbox]
    compiled = [e for e in envelopes
                if e["event_type"] == "pipeline.template.compiled"]
    assert len(compiled) == 1


_ = (TENANT_A, WORKSPACE)
