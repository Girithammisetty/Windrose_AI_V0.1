"""pack-service inc4 / BRD 53: attach a per-agent security envelope to a FIXED
agent via PUT /registry/tenants/self/agents/{agent_key}, and verify the PUT is a
partial upsert — a facet the body omits is preserved, not reset. This closes the
bug where setting prompt_params (or enable) silently wiped guardrail_policy, and
gives pack-service a surface to materialize pack guardrails onto the agents a
pack specializes (no custom-agent creation required)."""

from __future__ import annotations

import httpx
import pytest

from app.container import build_container
from app.main import create_app
from tests.conftest import TENANT_A, make_settings, make_token


class _CapAuthz:
    async def allow(self, *, subject, action, tenant, resource_urn=None, workspace_id=None):
        return subject.get("id") == "u-admin" and action.startswith("ai.agent.")


@pytest.fixture
async def client_and_container():
    c = build_container(make_settings(), mode="memory", authz=_CapAuthz())
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, c


def _auth(sub="u-admin", tenant=TENANT_A):
    return {"Authorization": f"Bearer {make_token(sub=sub, tenant_id=tenant, scopes=[])}"}


_KEY = "case-triage"


async def test_put_attaches_guardrail_and_partial_upsert_preserves(client_and_container):
    client, c = client_and_container

    # 1. prompt_params only, no envelope.
    r1 = await client.put(f"/api/v1/registry/tenants/self/agents/{_KEY}",
                          json={"enabled": True, "prompt_params": {"persona": "AP Analyst"}},
                          headers=_auth())
    assert r1.status_code == 200, r1.text
    assert r1.json()["data"]["guardrail_policy"] == {}

    # 2. attach an envelope WITHOUT resending prompt_params -> prompt_params preserved.
    r2 = await client.put(f"/api/v1/registry/tenants/self/agents/{_KEY}",
                          json={"guardrail_policy": {"budget": {"max_tokens_per_session": 60000},
                                                     "pii": {"redact": True}}},
                          headers=_auth())
    assert r2.status_code == 200, r2.text
    gp = r2.json()["data"]["guardrail_policy"]
    assert gp["budget"]["max_tokens_per_session"] == 60000
    assert gp["pii"]["redact"] is True
    cfg = await c.store.get_tenant_config(TENANT_A, _KEY)
    assert cfg.prompt_params["persona"] == "AP Analyst"   # preserved across the guardrail PUT

    # 3. a prompt_params-only PUT preserves the envelope (the wipe bug we fixed).
    r3 = await client.put(f"/api/v1/registry/tenants/self/agents/{_KEY}",
                          json={"prompt_params": {"persona": "AP Controls"}}, headers=_auth())
    assert r3.json()["data"]["guardrail_policy"]["budget"]["max_tokens_per_session"] == 60000

    # 4. an explicit empty policy CLEARS it (the pack-uninstall reversal path).
    r4 = await client.put(f"/api/v1/registry/tenants/self/agents/{_KEY}",
                          json={"guardrail_policy": {}}, headers=_auth())
    assert r4.json()["data"]["guardrail_policy"] == {}


async def test_put_guardrail_budget_clamped_to_ceiling(client_and_container):
    client, _ = client_and_container
    r = await client.put(f"/api/v1/registry/tenants/self/agents/{_KEY}",
                         json={"guardrail_policy": {"budget": {"max_tokens_per_session": 10_000_000}}},  # noqa: E501
                         headers=_auth())
    assert r.status_code == 200, r.text
    assert r.json()["data"]["guardrail_policy"]["budget"]["max_tokens_per_session"] == 200_000  # BR-8  # noqa: E501


async def test_put_guardrail_requires_agent_admin(client_and_container):
    client, _ = client_and_container
    r = await client.put(f"/api/v1/registry/tenants/self/agents/{_KEY}",
                         json={"guardrail_policy": {"pii": {"redact": True}}},
                         headers=_auth(sub="u-user"))
    assert r.status_code == 403
