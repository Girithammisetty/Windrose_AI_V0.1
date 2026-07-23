"""BRD 60 WS4 — the guardrail envelope (data_scope + PII) lifted to the shared
enforcement point at the proposal chokepoint, so it binds to EVERY write proposal
regardless of origin (internal graph OR external-intent ingress).

Two tiers: pure unit tests of `app.domain.guardrail`, and chokepoint tests that
drive `ProposalService.create_from_intent` with a tenant guardrail_policy set and
assert data-scope denial + PII redaction actually happen there (the code path the
external ingress shares)."""

from __future__ import annotations

import pytest

from app.container import build_container
from app.domain import guardrail as g
from app.domain.entities import Run, TenantAgentConfig, new_uuid
from app.domain.errors import GuardrailViolation
from app.graphs.base import WriteIntent
from tests.conftest import TENANT_A, make_settings

WS_IN = "11111111-1111-1111-1111-111111111111"
WS_OUT = "22222222-2222-2222-2222-222222222222"


# ---- pure module ---------------------------------------------------------

def test_no_scope_is_permissive():
    assert g.workspace_in_scope({}, WS_OUT) is True
    assert g.workspace_in_scope(None, None) is True
    g.enforce_data_scope({}, WS_OUT)  # no raise


def test_declared_scope_denies_outside_and_null():
    policy = {"data_scope": {"workspaces": [WS_IN]}}
    assert g.workspace_in_scope(policy, WS_IN) is True
    assert g.workspace_in_scope(policy, WS_OUT) is False
    # A write that declares NO workspace can't prove containment -> denied.
    assert g.workspace_in_scope(policy, None) is False
    with pytest.raises(GuardrailViolation):
        g.enforce_data_scope(policy, WS_OUT, agent_key="acme-ext-bot")


def test_pii_flag_detection_and_effect_redaction():
    assert g.pii_redaction_on({"pii": {"redact": True}}) is True
    assert g.pii_redaction_on({"pii": {"block_pii_egress": True}}) is True
    assert g.pii_redaction_on({}) is False
    eff = {"agent_summary": "call jane@acme.com or 555-123-4567",
           "authoritative_summary": "affects 1 resource",
           "citations": [{"detail": "SSN 123-45-6789 on file"}]}
    red = g.redact_effect(eff)
    assert "jane@acme.com" not in red["agent_summary"]
    assert "555-123-4567" not in red["agent_summary"]
    assert "123-45-6789" not in red["citations"][0]["detail"]
    # A server-derived summary with no PII is left intact.
    assert red["authoritative_summary"] == "affects 1 resource"


# ---- chokepoint (the path the external ingress shares) -------------------

def _container():
    return build_container(make_settings(), mode="memory")


async def _run(c, agent="acme-ext-bot"):
    r = Run(run_id=new_uuid(), tenant_id=TENANT_A, session_id=new_uuid(), agent_key=agent,
            agent_version=1, temporal_workflow_id=None, status="external_intent",
            principal_type="agent_autonomous", obo_sub=None)
    await c.store.create_run(r)
    return r


async def _set_policy(c, agent, policy):
    await c.store.put_tenant_config(TenantAgentConfig(
        tenant_id=TENANT_A, agent_key=agent, enabled=True, guardrail_policy=policy))


def _intent(workspace_id=None, rationale="routine", pe=None):
    return WriteIntent(
        tool_id="case.apply_disposition", tool_version="1.0.0", tier="write-proposal",
        side_effects="reversible", args={"case_id": "c-1"},
        rationale=rationale, affected_urns=[f"wr:{TENANT_A}:case:case/c-1"],
        predicted_effect=pe or {"summary": "assign", "blast_radius": 1},
        workspace_id=workspace_id)


async def test_chokepoint_denies_out_of_scope_workspace():
    c = _container()
    run = await _run(c)
    await _set_policy(c, "acme-ext-bot", {"data_scope": {"workspaces": [WS_IN]}})
    # Autonomous agent (no obo_user) -> the caller-gate is skipped, so data-scope
    # is the wall. An out-of-scope declared workspace fails closed, no proposal.
    with pytest.raises(GuardrailViolation):
        await c.proposal_service.create_from_intent(
            run=run, intent=_intent(workspace_id=WS_OUT), obo_user=None, auto_execute_policy={})


async def test_chokepoint_allows_in_scope_workspace():
    c = _container()
    run = await _run(c)
    await _set_policy(c, "acme-ext-bot", {"data_scope": {"workspaces": [WS_IN]}})
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=_intent(workspace_id=WS_IN), obo_user=None, auto_execute_policy={})
    assert prop.status == "pending"
    assert str(prop.workspace_id) == WS_IN


async def test_chokepoint_redacts_pii_in_stored_proposal():
    c = _container()
    run = await _run(c)
    await _set_policy(c, "acme-ext-bot", {"pii": {"redact": True}})
    intent = _intent(
        rationale="approve per adjuster jane@acme.com, phone 555-123-4567",
        pe={"summary": "notify jane@acme.com", "blast_radius": 1})
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=intent, obo_user=None, auto_execute_policy={})
    # The persisted proposal the approver sees has the direct identifiers scrubbed.
    assert "jane@acme.com" not in prop.rationale
    assert "555-123-4567" not in prop.rationale
    assert "jane@acme.com" not in prop.predicted_effect.get("agent_summary", "")


async def test_chokepoint_no_policy_is_noop():
    c = _container()
    run = await _run(c)
    # No tenant config at all -> permissive: proposal is created, nothing scrubbed.
    intent = _intent(workspace_id=WS_OUT, rationale="contact jane@acme.com")
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=intent, obo_user=None, auto_execute_policy={})
    assert prop.status == "pending"
    assert "jane@acme.com" in prop.rationale  # untouched when no pii policy
