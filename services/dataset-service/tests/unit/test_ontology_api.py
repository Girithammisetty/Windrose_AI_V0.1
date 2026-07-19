"""Domain ontology (inc11): governed entity-type registry — create/list/get/
delete, idempotency, relationships round-trip, validation."""

from __future__ import annotations

import pytest

from tests.conftest import WORKSPACE, auth

pytestmark = pytest.mark.asyncio


def _body(**over):
    b = {
        "workspace_id": WORKSPACE, "entity_key": "vendor", "name": "Vendor",
        "description": "A supplier the org pays.",
        "attributes": [{"name": "vendor_id", "data_type": "string"}],
        "relationships": [{"name": "invoices", "target": "invoice", "cardinality": "has_many"}],
    }
    b.update(over)
    return b


async def test_create_list_get_entity(client):
    r = await client.post("/api/v1/ontology/entities", json=_body(), headers=auth())
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["entity_key"] == "vendor"
    assert d["relationships"][0]["target"] == "invoice"  # relationships round-trip

    lr = await client.get(
        f"/api/v1/ontology/entities?filter[workspace_id]={WORKSPACE}", headers=auth())
    assert any(e["entity_key"] == "vendor" for e in lr.json()["data"])

    gr = await client.get(
        f"/api/v1/ontology/entities/vendor?filter[workspace_id]={WORKSPACE}", headers=auth())
    assert gr.status_code == 200
    assert gr.json()["data"]["attributes"][0]["name"] == "vendor_id"


async def test_create_is_idempotent(client):
    await client.post("/api/v1/ontology/entities", json=_body(), headers=auth())
    r2 = await client.post("/api/v1/ontology/entities", json=_body(name="changed"), headers=auth())
    assert r2.status_code == 201  # returns existing, not a duplicate
    lr = await client.get(
        f"/api/v1/ontology/entities?filter[workspace_id]={WORKSPACE}", headers=auth())
    assert len([e for e in lr.json()["data"] if e["entity_key"] == "vendor"]) == 1


async def test_delete_entity(client):
    await client.post("/api/v1/ontology/entities", json=_body(), headers=auth())
    dr = await client.delete(
        f"/api/v1/ontology/entities/vendor?filter[workspace_id]={WORKSPACE}", headers=auth())
    assert dr.status_code == 204
    gr = await client.get(
        f"/api/v1/ontology/entities/vendor?filter[workspace_id]={WORKSPACE}", headers=auth())
    assert gr.status_code == 404


async def test_create_requires_key_and_name(client):
    r = await client.post("/api/v1/ontology/entities", json=_body(entity_key=""), headers=auth())
    assert r.status_code >= 400
