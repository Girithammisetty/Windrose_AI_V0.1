"""BRD 55 inc1: decision outcome monitoring — capture realized outcomes joined
to a decision's provenance, and read decided-vs-realized effectiveness. Covers
the pure aggregator + the API (capture, join, effectiveness, isolation)."""

from __future__ import annotations

import httpx
import pytest

from app.container import build_container
from app.domain.entities import Proposal, new_uuid, now
from app.domain.outcomes import OutcomeLabel, compute_correct, effectiveness
from app.main import create_app
from tests.conftest import TENANT_A, TENANT_B, make_settings, make_token

# ---- pure aggregator ----

def test_compute_correct():
    assert compute_correct("deny", "deny") is True
    assert compute_correct("Deny", " deny ") is True      # case/space-insensitive
    assert compute_correct("deny", "approve") is False
    assert compute_correct(None, "deny") is None           # nothing decided
    assert compute_correct("", "deny") is None


def test_effectiveness_grouping():
    labs = [
        OutcomeLabel("1", "t", "d1", "case.apply_disposition", "won", "won", True, producer="agent-x"),
        OutcomeLabel("2", "t", "d2", "case.apply_disposition", "won", "lost", False, producer="agent-x"),
        OutcomeLabel("3", "t", "d3", "case.apply_disposition", "won", "won", True, producer="table-y"),
        OutcomeLabel("4", "t", "d4", "case.apply_disposition", None, "won", None, producer="table-y"),
    ]
    by_type = effectiveness(labs, by="decision_type")
    assert by_type[0]["total"] == 4 and by_type[0]["correct"] == 2
    assert by_type[0]["incorrect"] == 1 and by_type[0]["unknown"] == 1
    assert by_type[0]["effectiveness_rate"] == round(2 / 3, 4)  # unknown excluded

    by_prod = effectiveness(labs, by="producer")
    prod = {r["key"]: r for r in by_prod}
    assert prod["agent-x"]["effectiveness_rate"] == 0.5
    assert prod["table-y"]["correct"] == 1 and prod["table-y"]["unknown"] == 1


# ---- API ----

class _AllowAuthz:
    async def allow(self, *, subject, action, tenant, resource_urn=None, workspace_id=None):
        return True


@pytest.fixture
async def client_and_container():
    c = build_container(make_settings(), mode="memory", authz=_AllowAuthz())
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, c


def _auth(tenant=TENANT_A, sub="u-mgr"):
    return {"Authorization": f"Bearer {make_token(sub=sub, tenant_id=tenant, scopes=[])}"}


async def _seed_proposal(c, tenant=TENANT_A, agent="cust-x-copilot",
                         disposition="escalate_fraud_review"):
    from datetime import UTC, datetime
    pid = new_uuid()
    await c.store.create_proposal(Proposal(
        proposal_id=pid, tenant_id=tenant, session_id=new_uuid(), run_id=new_uuid(),
        agent_key=agent, agent_version=1, obo_user="u-77",
        tool_id="case.apply_disposition", tool_version="1.2.0", tier="write-proposal",
        side_effects="reversible",
        args={"case_id": "c-91", "disposition_code": disposition, "severity": "high"},
        rationale="x", affected_urns=[f"wr:{tenant}:case:case/c-91"],
        predicted_effect={"summary": "x", "reversibility": "reversible", "blast_radius": 1},
        expires_at=datetime.fromtimestamp(now().timestamp() + 3600, tz=UTC),
        status="approved"))
    return pid


async def test_mark_outcome_joins_proposal_provenance(client_and_container):
    client, c = client_and_container
    pid = await _seed_proposal(c, disposition="escalate_fraud_review")
    r = await client.post(f"/api/v1/decisions/{pid}/outcome",
                          json={"realized_outcome": "escalate_fraud_review",
                                "note": "SIU confirmed fraud"}, headers=_auth())
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    # decided outcome + producer were joined from the proposal; agreement computed
    assert d["decision_type"] == "case.apply_disposition"
    assert d["producer"] == "cust-x-copilot"
    assert d["decided_outcome"] == "escalate_fraud_review"
    assert d["correct"] is True


async def test_mark_outcome_disagreement(client_and_container):
    client, c = client_and_container
    pid = await _seed_proposal(c, disposition="escalate_fraud_review")
    r = await client.post(f"/api/v1/decisions/{pid}/outcome",
                          json={"realized_outcome": "deny_no_error_found"}, headers=_auth())
    assert r.json()["data"]["correct"] is False


async def test_effectiveness_read(client_and_container):
    client, c = client_and_container
    p1 = await _seed_proposal(c, disposition="escalate_fraud_review")
    p2 = await _seed_proposal(c, disposition="escalate_fraud_review")
    await client.post(f"/api/v1/decisions/{p1}/outcome",
                      json={"realized_outcome": "escalate_fraud_review"}, headers=_auth())
    await client.post(f"/api/v1/decisions/{p2}/outcome",
                      json={"realized_outcome": "deny_no_error_found"}, headers=_auth())
    r = await client.get("/api/v1/decision-effectiveness?by=decision_type", headers=_auth())
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["labeled_decisions"] == 2
    row = d["groups"][0]
    assert row["total"] == 2 and row["correct"] == 1 and row["effectiveness_rate"] == 0.5


async def test_label_annotates_not_mutates_and_upserts(client_and_container):
    client, c = client_and_container
    pid = await _seed_proposal(c)
    await client.post(f"/api/v1/decisions/{pid}/outcome",
                      json={"realized_outcome": "deny_no_error_found"}, headers=_auth())
    # a corrected outcome supersedes (BR-1 annotate; one label per decision)
    await client.post(f"/api/v1/decisions/{pid}/outcome",
                      json={"realized_outcome": "escalate_fraud_review"}, headers=_auth())
    got = await client.get(f"/api/v1/decisions/{pid}/outcome", headers=_auth())
    assert got.json()["data"]["realized_outcome"] == "escalate_fraud_review"
    # the underlying proposal is untouched
    prop = await c.store.get_proposal(TENANT_A, pid)
    assert prop.status == "approved"


async def test_tenant_isolation(client_and_container):
    client, c = client_and_container
    pid = await _seed_proposal(c, tenant=TENANT_A)
    await client.post(f"/api/v1/decisions/{pid}/outcome",
                      json={"realized_outcome": "x", "decision_type": "case.apply_disposition"},
                      headers=_auth(tenant=TENANT_A))
    # TENANT_B sees no labels + cannot read A's
    eff = await client.get("/api/v1/decision-effectiveness", headers=_auth(tenant=TENANT_B))
    assert eff.json()["data"]["labeled_decisions"] == 0
    g = await client.get(f"/api/v1/decisions/{pid}/outcome", headers=_auth(tenant=TENANT_B))
    assert g.status_code == 404
