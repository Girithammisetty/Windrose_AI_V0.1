"""BRD 54 DM-FR-020/030/040/050: decision-model API — author-time validation,
governed evaluate → four-eyes proposal, dry-run (no side effect), tenant
isolation. Uses the in-memory container double."""

from __future__ import annotations

import httpx
import pytest

from app.container import build_container
from app.main import create_app
from tests.conftest import TENANT_A, TENANT_B, make_settings, make_token


class _CaseReaderWithCatalog:
    """Case + disposition-catalog double for validation + resolution."""
    async def get_case(self, *, tenant_id, case_id, auth_token) -> dict:
        return {"id": case_id, "workspace_id": "ws-1",
                "display_projection": {"dispute_type": "fraud_unauthorized",
                                       "amount": "2450.00"}}

    async def list_cases(self, *, tenant_id, workspace_id, limit=100, auth_token):
        # a mixed worklist: one big-fraud (matches rule #0), one small (default only)
        return [
            {"id": "c-1", "workspace_id": "ws-1",
             "display_projection": {"dispute_type": "fraud_unauthorized", "amount": "2450.00"}},
            {"id": "c-2", "workspace_id": "ws-1",
             "display_projection": {"dispute_type": "billing_error", "amount": "40.00"}},
        ][:limit]

    async def list_dispositions(self, *, tenant_id, auth_token) -> list[dict]:
        return [{"id": "disp-1", "code": "escalate_fraud_review", "label": "Escalate"},
                {"id": "disp-2", "code": "deny_no_error_found", "label": "Deny"}]


class _AllowAuthz:
    async def allow(self, *, subject, action, tenant, resource_urn=None, workspace_id=None):
        return True


@pytest.fixture
async def client_and_container():
    c = build_container(make_settings(), mode="memory", authz=_AllowAuthz(),
                        case_reader=_CaseReaderWithCatalog())
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, c


def _auth(tenant=TENANT_A, sub="u-mgr"):
    return {"Authorization": f"Bearer {make_token(sub=sub, tenant_id=tenant, scopes=[])}"}


_VALID = {"name": "Reg E fraud table",
          "rules": [{"when": [{"column": "dispute_type", "op": "eq", "value": "fraud_unauthorized"},
                              {"column": "amount", "op": "gt", "value": 1000}],
                     "then": {"disposition_code": "escalate_fraud_review", "severity": "high"},
                     "note": "big CNP fraud"}],
          "default_outcome": {"disposition_code": "deny_no_error_found", "severity": "medium"}}


async def _publish(client, body=_VALID, author="u-author", approver="u-approver") -> str:
    """Create a draft then four-eyes-approve it → a live (published) table id."""
    mid = (await client.post("/api/v1/decision-models", json=body,
                             headers=_auth(sub=author))).json()["data"]["id"]
    r = await client.post(f"/api/v1/decision-models/{mid}/approve",
                          headers=_auth(sub=approver))
    assert r.status_code == 200, r.text
    return mid


async def test_create_lands_draft(client_and_container):
    client, _ = client_and_container
    r = await client.post("/api/v1/decision-models", json=_VALID, headers=_auth())
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    # governance (inc3): create does NOT go live — it lands draft, unapproved.
    assert d["status"] == "draft" and d["version"] == 1
    assert d["approved_by"] is None and len(d["rules"]) == 1


async def test_create_rejects_unknown_disposition_code(client_and_container):
    client, _ = client_and_container
    bad = {**_VALID, "rules": [{"when": [{"column": "amount", "op": "gt", "value": 1}],
                                "then": {"disposition_code": "not_a_real_code",
                                         "severity": "high"}}]}
    r = await client.post("/api/v1/decision-models", json=bad, headers=_auth())
    assert r.status_code >= 400 and "catalog" in r.text


async def test_create_rejects_empty_rules(client_and_container):
    client, _ = client_and_container
    r = await client.post("/api/v1/decision-models",
                          json={**_VALID, "rules": []}, headers=_auth())
    assert r.status_code >= 400 and "rule" in r.text


async def test_evaluate_dry_run_makes_no_proposal(client_and_container):
    client, c = client_and_container
    mid = await _publish(client)
    r = await client.post(f"/api/v1/decision-models/{mid}/evaluate?dry_run=true",
                          json={"case_id": "c-91"}, headers=_auth())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["matched"] is True and d["proposal_id"] is None
    assert d["outcome"]["disposition_code"] == "escalate_fraud_review"
    assert "rule #0 fired" in d["explanation"]
    assert c.bus.of_type("proposal.created") == []      # nothing governed created


async def test_evaluate_creates_governed_proposal(client_and_container):
    client, c = client_and_container
    mid = await _publish(client)
    r = await client.post(f"/api/v1/decision-models/{mid}/evaluate",
                          json={"case_id": "c-91"}, headers=_auth())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["proposal_id"] and d["proposal_status"] == "pending"
    # It went through the SHARED ProposalService (four-eyes, guardrail, audit).
    assert len(c.bus.of_type("proposal.created")) == 1
    prop = await c.store.get_proposal(TENANT_A, d["proposal_id"])
    assert prop.tool_id == "case.apply_disposition"
    assert prop.args["disposition_id"] == "disp-1"      # resolved from catalog
    assert prop.args["severity"] == "high"
    assert "Decision model" in prop.args["resolution_note"]
    # per-decision trace (inc3): version + inputs + fired rule captured on the proposal
    trace = prop.predicted_effect["decision_trace"]
    assert trace["model_version"] == 1 and trace["rule_index"] == 0
    assert trace["inputs"]["dispute_type"] == "fraud_unauthorized"  # referenced col snapshot
    assert trace["outcome"]["disposition_code"] == "escalate_fraud_review"


async def test_tenant_isolation(client_and_container):
    client, _ = client_and_container
    mid = await _publish(client)
    # TENANT_B cannot fetch or evaluate TENANT_A's model.
    g = await client.get(f"/api/v1/decision-models/{mid}", headers=_auth(tenant=TENANT_B))
    assert g.status_code == 404
    e = await client.post(f"/api/v1/decision-models/{mid}/evaluate",
                          json={"case_id": "c-91"}, headers=_auth(tenant=TENANT_B))
    assert e.status_code == 404


# ---- inc3 lifecycle governance (DM-FR-071/072) ------------------------------

async def test_draft_cannot_be_evaluated(client_and_container):
    client, _ = client_and_container
    mid = (await client.post("/api/v1/decision-models", json=_VALID,
                             headers=_auth())).json()["data"]["id"]
    # a draft is not live — evaluate + batch are refused until approved
    e = await client.post(f"/api/v1/decision-models/{mid}/evaluate",
                          json={"case_id": "c-91"}, headers=_auth())
    assert e.status_code >= 400 and "published" in e.text
    b = await client.post(f"/api/v1/decision-models/{mid}/batch-evaluate",
                          json={"workspace_id": "ws-1"}, headers=_auth())
    assert b.status_code >= 400 and "published" in b.text


async def test_four_eyes_author_cannot_approve_own(client_and_container):
    client, _ = client_and_container
    mid = (await client.post("/api/v1/decision-models", json=_VALID,
                             headers=_auth(sub="u-author"))).json()["data"]["id"]
    # the author approving their own table is refused (four-eyes on the logic)
    r = await client.post(f"/api/v1/decision-models/{mid}/approve",
                          headers=_auth(sub="u-author"))
    assert r.status_code == 403 and "four-eyes" in r.text
    # a different user can
    ok = await client.post(f"/api/v1/decision-models/{mid}/approve",
                           headers=_auth(sub="u-approver"))
    assert ok.status_code == 200
    d = ok.json()["data"]
    assert d["status"] == "published" and d["approved_by"] == "u-approver"


async def test_new_version_is_draft_and_prior_immutable(client_and_container):
    client, _ = client_and_container
    mid = await _publish(client)  # v1 published
    # edit → v2 draft; v1 stays published (what you approve is what runs)
    v2 = await client.post(f"/api/v1/decision-models/{mid}/versions",
                           json={"rules": [{"when": [{"column": "amount", "op": "gt", "value": 5000}],  # noqa: E501
                                            "then": {"disposition_code": "escalate_fraud_review",
                                                     "severity": "critical"}}]},
                           headers=_auth(sub="u-author"))
    assert v2.status_code == 201, v2.text
    dv2 = v2.json()["data"]
    assert dv2["version"] == 2 and dv2["status"] == "draft"
    v1 = (await client.get(f"/api/v1/decision-models/{mid}", headers=_auth())).json()["data"]
    assert v1["status"] == "published" and v1["version"] == 1
    # approving v2 archives v1 → exactly one live version
    await client.post(f"/api/v1/decision-models/{dv2['id']}/approve", headers=_auth(sub="u-approver"))  # noqa: E501
    v1b = (await client.get(f"/api/v1/decision-models/{mid}", headers=_auth())).json()["data"]
    assert v1b["status"] == "archived"
    # the change log lists both, newest first
    hist = (await client.get(f"/api/v1/decision-models/{dv2['id']}/versions",
                             headers=_auth())).json()["data"]
    assert [h["version"] for h in hist] == [2, 1]


# ---- inc2 batch evaluation (DM-FR-060) --------------------------------------

async def test_batch_preview_is_dry_run(client_and_container):
    client, c = client_and_container
    mid = await _publish(client)
    r = await client.post(f"/api/v1/decision-models/{mid}/batch-evaluate",
                          json={"workspace_id": "ws-1"}, headers=_auth())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["proposed"] is False
    # c-1 fires rule #0; c-2 falls through to the default outcome (both matched).
    assert d["summary"]["cases"] == 2 and d["summary"]["matched"] == 2
    assert d["summary"]["by_outcome"] == {"escalate_fraud_review": 1,
                                          "deny_no_error_found": 1}
    assert d["summary"]["proposals_created"] == 0
    # dry-run creates NO governed proposals
    assert c.bus.of_type("proposal.created") == []
    c1 = next(r for r in d["results"] if r["case_id"] == "c-1")
    assert c1["rule_index"] == 0
    assert c1["outcome"]["disposition_code"] == "escalate_fraud_review"
    c2 = next(r for r in d["results"] if r["case_id"] == "c-2")
    assert c2["rule_index"] is None  # default outcome, no rule fired
    assert c2["outcome"]["disposition_code"] == "deny_no_error_found"


async def test_batch_propose_creates_one_proposal_per_match(client_and_container):
    client, c = client_and_container
    mid = await _publish(client)
    r = await client.post(f"/api/v1/decision-models/{mid}/batch-evaluate?propose=true",
                          json={"case_ids": ["c-1", "c-2"]}, headers=_auth())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    # get_case double returns fraud for every id, so both match here → 2 proposals
    assert d["proposed"] is True and d["summary"]["proposals_created"] == 2
    assert len(c.bus.of_type("proposal.created")) == 2
    for row in d["results"]:
        assert row["proposal_id"] and row["proposal_status"] == "pending"


async def test_batch_isolation(client_and_container):
    client, _ = client_and_container
    mid = await _publish(client)
    r = await client.post(f"/api/v1/decision-models/{mid}/batch-evaluate",
                          json={"workspace_id": "ws-1"}, headers=_auth(tenant=TENANT_B))
    assert r.status_code == 404
