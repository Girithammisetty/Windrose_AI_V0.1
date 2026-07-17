"""Kill-switch admin routes (ART-FR-073): create, list, lift. Exercises the
route-level authz (operator/tenant-admin) and the list's visibility rule
(tenant admin -> own tenant + global; operator -> everything) over the
in-memory store double."""

from __future__ import annotations

import httpx
import pytest

from app.agents.catalog import seed_catalog
from app.container import build_container
from app.main import create_app
from tests.conftest import TENANT_A, TENANT_B, make_settings, make_token


class _CapAuthz:
    """Capability-based authz double (P4): kill-switch routes now authorize on the
    rbac ai.agent.* capability, not a JWT scope. Every tenant admin in these tests
    holds it; only the deliberately-unprivileged "u-plain" does not. (Operators
    still bypass by scope, so they are not consulted here.)"""

    async def allow(self, *, subject, action, tenant, resource_urn=None, workspace_id=None):
        return subject.get("id") != "u-plain" and action.startswith("ai.agent.")


@pytest.fixture
async def client_and_container():
    c = build_container(make_settings(), mode="memory", authz=_CapAuthz())
    await seed_catalog(c.store, c.signing_key)
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, c


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_tenant_admin_creates_lists_and_lifts_own_kill_switch(client_and_container):
    client, _ = client_and_container
    admin = make_token(sub="u-admin", tenant_id=TENANT_A, scopes=["tenant.admin"])

    r = await client.post("/api/v1/registry/kill-switches", headers=_auth(admin),
                          json={"agent_key": "case-triage", "reason": "INC-1"})
    assert r.status_code == 200, r.text
    kill_id = r.json()["data"]["kill_id"]
    assert r.json()["data"]["active"] is True

    r = await client.get("/api/v1/registry/kill-switches", headers=_auth(admin))
    assert r.status_code == 200
    rows = r.json()["data"]
    assert any(k["kill_id"] == kill_id for k in rows)
    row = next(k for k in rows if k["kill_id"] == kill_id)
    assert row["reason"] == "INC-1"
    assert row["tenant_id"] == TENANT_A

    r = await client.delete(f"/api/v1/registry/kill-switches/{kill_id}", headers=_auth(admin))
    assert r.status_code == 200
    assert r.json()["data"]["active"] is False

    r = await client.get("/api/v1/registry/kill-switches", headers=_auth(admin))
    assert not any(k["kill_id"] == kill_id for k in r.json()["data"])


async def test_create_requires_reason(client_and_container):
    client, _ = client_and_container
    admin = make_token(sub="u-admin", tenant_id=TENANT_A, scopes=["tenant.admin"])
    r = await client.post("/api/v1/registry/kill-switches", headers=_auth(admin),
                          json={"agent_key": "case-triage"})
    assert r.status_code == 422


async def test_create_requires_operator_or_tenant_admin(client_and_container):
    client, _ = client_and_container
    plain = make_token(sub="u-plain", tenant_id=TENANT_A, scopes=[])
    r = await client.post("/api/v1/registry/kill-switches", headers=_auth(plain),
                          json={"agent_key": "case-triage", "reason": "x"})
    assert r.status_code == 403


async def test_list_requires_operator_or_tenant_admin(client_and_container):
    client, _ = client_and_container
    plain = make_token(sub="u-plain", tenant_id=TENANT_A, scopes=[])
    r = await client.get("/api/v1/registry/kill-switches", headers=_auth(plain))
    assert r.status_code == 403


async def test_tenant_admin_cannot_see_other_tenants_kill_switch(client_and_container):
    client, _ = client_and_container
    admin_a = make_token(sub="u-a", tenant_id=TENANT_A, scopes=["tenant.admin"])
    admin_b = make_token(sub="u-b", tenant_id=TENANT_B, scopes=["tenant.admin"])

    r = await client.post("/api/v1/registry/kill-switches", headers=_auth(admin_b),
                          json={"agent_key": "case-triage", "reason": "tenant-b-only"})
    kill_id_b = r.json()["data"]["kill_id"]

    r = await client.get("/api/v1/registry/kill-switches", headers=_auth(admin_a))
    assert not any(k["kill_id"] == kill_id_b for k in r.json()["data"])


async def test_operator_sees_every_tenants_kill_switch(client_and_container):
    client, _ = client_and_container
    admin_b = make_token(sub="u-b", tenant_id=TENANT_B, scopes=["tenant.admin"])
    operator = make_token(sub="svc:ops", tenant_id=TENANT_A, typ="service", scopes=["operator"])

    r = await client.post("/api/v1/registry/kill-switches", headers=_auth(admin_b),
                          json={"agent_key": "case-triage", "reason": "tenant-b-visible-to-op"})
    kill_id_b = r.json()["data"]["kill_id"]

    r = await client.get("/api/v1/registry/kill-switches", headers=_auth(operator))
    assert any(k["kill_id"] == kill_id_b for k in r.json()["data"])


async def test_operator_can_create_platform_wide_kill(client_and_container):
    client, _ = client_and_container
    operator = make_token(sub="svc:ops", tenant_id=TENANT_A, typ="service", scopes=["operator"])
    r = await client.post("/api/v1/registry/kill-switches", headers=_auth(operator),
                          json={"scope": "agent", "agent_key": "case-triage",
                                "reason": "platform-wide"})
    assert r.status_code == 200
    kill_id = r.json()["data"]["kill_id"]

    # A different tenant's admin must see this GLOBAL kill (tenant_id IS NULL rows
    # stay visible to every tenant per the RLS policy's own stated intent).
    other_admin = make_token(sub="u-b", tenant_id=TENANT_B, scopes=["tenant.admin"])
    r = await client.get("/api/v1/registry/kill-switches", headers=_auth(other_admin))
    assert any(k["kill_id"] == kill_id for k in r.json()["data"])
