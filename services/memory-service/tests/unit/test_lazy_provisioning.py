"""Lazy tenant provisioning (BR-14 fallback).

Tenants whose ``tenant.provisioned`` event was never consumed (e.g. provisioned
before the wave-1 lifespan fix started the Kafka consumers) must not 500 on
first use: retrieve/write ensure the tenant schema + defaults idempotently.
"""

from __future__ import annotations

import pytest

from app.events.envelope import make_envelope
from tests.conftest import USER_A, auth, prov

pytestmark = pytest.mark.asyncio

# A tenant id no fixture ever provisions — simulates a pre-existing tenant.
FRESH_TENANT = "99999999-9999-4999-8999-999999999999"


async def test_retrieve_fresh_tenant_returns_empty_list_not_500(client, container):
    assert not await container.store.tenant_ready(FRESH_TENANT)
    r = await client.post("/api/v1/retrieve", json={
        "query_text": "prior water damage claims", "scopes": ["user"],
        "corpora": ["resolved_cases"]}, headers=auth(FRESH_TENANT, USER_A))
    assert r.status_code == 200, r.text
    assert r.json()["data"] == []
    # First use provisioned the tenant: schema + standard corpora + policy.
    assert await container.store.tenant_ready(FRESH_TENANT)
    assert await container.store.get_corpus(FRESH_TENANT, "resolved_cases") is not None
    assert await container.store.get_policy(FRESH_TENANT) is not None


async def test_write_fresh_tenant_provisions_then_persists(client, container):
    assert not await container.store.tenant_ready(FRESH_TENANT)
    r = await client.post("/api/v1/memories", json={
        "scope": "user", "scope_ref": USER_A, "content": "first write provisions",
        "provenance": prov("user_explicit", user_id=USER_A)},
        headers=auth(FRESH_TENANT, USER_A))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "active"
    assert await container.store.tenant_ready(FRESH_TENANT)


async def test_case_resolved_event_provisions_and_ingests(container):
    """Learning-loop write path: the case-events consumer must provision the
    tenant (schema + resolved_cases corpus) and land the chunk write even when
    no tenant.provisioned event was ever consumed."""
    assert not await container.store.tenant_ready(FRESH_TENANT)
    env = make_envelope(
        event_type="case.resolved", tenant_id=FRESH_TENANT,
        actor={"type": "user", "id": USER_A},
        resource_urn=f"wr:{FRESH_TENANT}:case:case/c-100",
        payload={"resolution_narrative": "Leak traced to supply line; approved.",
                 "disposition": "approved", "case_type": "water_damage",
                 "authored_by": USER_A})
    await container.consumer.handle(env)
    assert await container.store.tenant_ready(FRESH_TENANT)
    assert await container.store.count_chunks(FRESH_TENANT, "resolved_cases") >= 1
