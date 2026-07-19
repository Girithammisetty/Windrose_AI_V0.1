"""Agent-version publish requires a GENUINELY passing eval gate (P1 hardening).

Previously publish only checked eval_gate_result_id was non-null, so a hard-coded
placeholder satisfied it. Now the id is verified against eval-service (FakeEvalGate
here): a fake/failed gate is rejected; a real passing gate is accepted; an operator
may still force with a reason.
"""

from __future__ import annotations

import httpx
import pytest

from app.adapters.fakes import FakeEvalGate
from app.container import build_container
from app.domain.entities import AgentDefinition, AgentVersion
from app.graphs.base import graph_digest
from app.main import create_app
from tests.conftest import TENANT_A, make_settings, make_token


@pytest.fixture
async def client_and_container():
    # Only "real-pass" counts as a genuinely passing eval gate.
    c = build_container(make_settings(), mode="memory",
                        eval_gate=FakeEvalGate(passing={"real-pass"}))
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, c


def _operator() -> dict:
    tok = make_token(sub="op-1", tenant_id=TENANT_A, scopes=["operator"])
    return {"Authorization": f"Bearer {tok}"}


async def _draft(c, agent_key: str, gate_id: str | None):
    await c.store.upsert_agent_definition(AgentDefinition(
        agent_key=agent_key, display_name=agent_key.upper(), description="d",
        owner_team="platform-ai", default_write_mode="proposal", status="draft"))
    await c.store.create_agent_version(AgentVersion(
        agent_key=agent_key, version=2, graph_ref="triage.v1",
        graph_digest=graph_digest("triage.v1"), eval_gate_result_id=gate_id, status="draft"))


async def _publish(client, agent_key, body=None):
    return await client.post(
        f"/api/v1/registry/agents/{agent_key}/versions/2/publish",
        json=body or {}, headers=_operator())


async def test_publish_blocked_when_gate_is_a_placeholder(client_and_container):
    client, c = client_and_container
    await _draft(c, "a-fake", gate_id="seed-gate-pass")  # not a real passing gate
    r = await _publish(client, "a-fake")
    assert r.status_code >= 400
    assert (await c.store.get_agent_version("a-fake", 2)).status == "draft"


async def test_publish_blocked_when_no_gate_attached(client_and_container):
    client, c = client_and_container
    await _draft(c, "a-none", gate_id=None)
    assert (await _publish(client, "a-none")).status_code >= 400


async def test_publish_allowed_with_verified_passing_gate(client_and_container):
    client, c = client_and_container
    await _draft(c, "a-ok", gate_id="real-pass")
    r = await _publish(client, "a-ok")
    assert r.status_code == 200, r.text
    assert (await c.store.get_agent_version("a-ok", 2)).status == "published"


async def test_force_publish_requires_reason_then_bypasses(client_and_container):
    client, c = client_and_container
    await _draft(c, "a-force", gate_id="seed-gate-pass")
    assert (await _publish(client, "a-force", {"force": True})).status_code >= 400  # no reason
    r = await _publish(client, "a-force", {"force": True, "reason": "hotfix rollback"})
    assert r.status_code == 200, r.text
    assert (await c.store.get_agent_version("a-force", 2)).status == "published"
