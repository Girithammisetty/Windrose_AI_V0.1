"""Proposal framework + HITL (ART-FR-041..046, BR-12, AC-4/AC-5/AC-6)."""

from __future__ import annotations

import jwt as pyjwt
import pytest

from app.constants import GRANT_ISSUER
from app.container import build_container
from app.domain.canonical import args_digest
from app.domain.entities import Run, new_uuid
from app.domain.errors import Conflict
from app.graphs.base import WriteIntent
from tests.conftest import TENANT_A, make_settings


def _container():
    return build_container(make_settings(), mode="memory")


async def _run(c, tenant=TENANT_A, agent="case-triage"):
    r = Run(run_id=new_uuid(), tenant_id=tenant, session_id=new_uuid(), agent_key=agent,
            agent_version=1, temporal_workflow_id=None, status="running",
            principal_type="user_obo", obo_sub="u-77")
    await c.store.create_run(r)
    return r


def _intent():
    return WriteIntent(
        tool_id="case.apply_disposition", tool_version="1.0.0", tier="write-proposal",
        side_effects="reversible",
        args={"case_id": "c-91", "severity": "high", "assignee_id": "u-dana"},
        rationale="Vendor pattern matches 14 resolved cases.",
        affected_urns=[f"wr:{TENANT_A}:case:case/c-91"],
        predicted_effect={"summary": "assign + severity high", "reversibility": "reversible",
                          "blast_radius": 1})


async def test_create_proposal_pending_no_execution():
    c = _container()
    run = await _run(c)
    prop, executed = await c.proposal_service.create_from_intent(
        run=run, intent=_intent(), obo_user="u-77", auto_execute_policy={})
    assert prop.status == "pending" and executed is False
    assert c.tool_client.calls == []  # nothing executed before approval
    events = c.bus.of_type("proposal.created")
    assert len(events) == 1


async def test_approve_issues_signed_grant_and_executes():
    c = _container()
    run = await _run(c)
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=_intent(), obo_user="u-77", auto_execute_policy={})
    decided = await c.proposal_service.decide(
        tenant_id=TENANT_A, proposal_id=prop.proposal_id, actor_sub="u-super",
        action="approve")
    assert decided.status == "approved"
    # tool-plane was called WITH a signed grant bound to the exact args
    assert len(c.tool_client.calls) == 1
    call = c.tool_client.calls[0]
    grant = call["grant"]
    assert grant is not None
    claims = pyjwt.decode(grant, c.signing_key.public_pem, algorithms=["RS256"],
                          issuer=GRANT_ISSUER, options={"require": ["exp"]})
    assert claims["tool_id"] == "case.apply_disposition"
    assert claims["tenant_id"] == TENANT_A
    assert claims["tier"] == "write-proposal"
    assert claims["args_digest"] == args_digest(call["arguments"])
    assert claims["sub"] == "u-super"


async def test_reject_executes_nothing():
    c = _container()
    run = await _run(c)
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=_intent(), obo_user="u-77", auto_execute_policy={})
    decided = await c.proposal_service.decide(
        tenant_id=TENANT_A, proposal_id=prop.proposal_id, actor_sub="u-super",
        action="reject", message="not this vendor")
    assert decided.status == "rejected"
    assert c.tool_client.calls == []


async def test_edit_args_executes_edited_and_records_diff():
    c = _container()
    run = await _run(c)
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=_intent(), obo_user="u-77", auto_execute_policy={})
    decided = await c.proposal_service.decide(
        tenant_id=TENANT_A, proposal_id=prop.proposal_id, actor_sub="u-super",
        action="edit_args",
        edited_args={"case_id": "c-91", "severity": "medium", "assignee_id": "u-dana"})
    assert decided.status == "edited_approved"
    assert decided.decision["diff"] == [{"field": "severity", "from": "high", "to": "medium"}]
    grant = c.tool_client.calls[0]["grant"]
    claims = pyjwt.decode(grant, c.signing_key.public_pem, algorithms=["RS256"],
                          issuer=GRANT_ISSUER, options={"require": ["exp"]})
    assert claims["args_digest"] == args_digest(
        {"case_id": "c-91", "severity": "medium", "assignee_id": "u-dana"})


async def test_decision_first_wins_conflict():
    c = _container()
    run = await _run(c)
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=_intent(), obo_user="u-77", auto_execute_policy={})
    await c.proposal_service.decide(tenant_id=TENANT_A, proposal_id=prop.proposal_id,
                                    actor_sub="u-super", action="approve")
    with pytest.raises(Conflict):
        await c.proposal_service.decide(tenant_id=TENANT_A, proposal_id=prop.proposal_id,
                                        actor_sub="u-other", action="reject")


async def test_auto_execute_reversible_policy():
    c = _container()
    run = await _run(c, agent="dashboard-designer")
    policy = {"dashboard-designer": {"write-proposal": {"reversible": "auto"}}}
    prop, executed = await c.proposal_service.create_from_intent(
        run=run, intent=_intent(), obo_user="u-77", auto_execute_policy=policy)
    assert executed is True
    assert prop.decision["actor"] == "policy:auto"
    assert len(c.tool_client.calls) == 1


async def test_self_approval_denied_by_default():
    from app.domain.errors import PermissionDenied
    c = _container()
    run = await _run(c)
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=_intent(), obo_user="u-77", auto_execute_policy={})
    with pytest.raises(PermissionDenied):
        await c.proposal_service.decide(tenant_id=TENANT_A, proposal_id=prop.proposal_id,
                                        actor_sub="u-77", action="approve")


async def test_autonomous_proposal_requires_distinct_human_approver():
    """Four-eyes on a FULLY-AUTONOMOUS proposal (no obo_user): the same-person
    self-approval guard is a no-op (there is no obo_user to compare), so
    _check_eligibility must still require an explicit distinct human approver —
    a non-empty principal that is not the proposing agent's own identity — and a
    genuine second party must be able to approve."""
    from app.domain.errors import PermissionDenied

    c = _container()
    run = await _run(c, agent="ml-engineer")
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=_intent(), obo_user=None, auto_execute_policy={})
    assert prop.obo_user is None and prop.agent_key == "ml-engineer"

    # No approver principal at all -> denied (no verified second party).
    with pytest.raises(PermissionDenied):
        await c.proposal_service.decide(tenant_id=TENANT_A, proposal_id=prop.proposal_id,
                                        actor_sub="", action="approve")
    # The proposing agent's own identity cannot rubber-stamp its own proposal.
    with pytest.raises(PermissionDenied):
        await c.proposal_service.decide(tenant_id=TENANT_A, proposal_id=prop.proposal_id,
                                        actor_sub="ml-engineer", action="approve")
    # A distinct human approver succeeds and executes.
    decided = await c.proposal_service.decide(
        tenant_id=TENANT_A, proposal_id=prop.proposal_id, actor_sub="u-super",
        action="approve")
    assert decided.status == "approved"
    assert len(c.tool_client.calls) == 1


async def test_approver_eligibility_denied_by_authz(monkeypatch):
    """ART-FR-044 / AC-12: an approver lacking the underlying permission on an
    affected URN is denied (OPA _check_eligibility -> authz.allow-per-URN),
    the proposal stays pending, and nothing executes."""
    from app.adapters.authz import DenyURNAuthz
    from app.domain.errors import PermissionDenied

    urn = f"wr:{TENANT_A}:case:case/c-91"
    # u-super is NOT eligible on the affected URN (random/ineligible approver)
    c = build_container(make_settings(), mode="memory",
                        authz=DenyURNAuthz(denied={("u-super", urn)}))
    run = await _run(c)
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=_intent(), obo_user="u-77", auto_execute_policy={})

    with pytest.raises(PermissionDenied):
        await c.proposal_service.decide(tenant_id=TENANT_A, proposal_id=prop.proposal_id,
                                        actor_sub="u-super", action="approve")
    # proposal remains actionable; no grant issued, no tool call
    still = await c.store.get_proposal(TENANT_A, prop.proposal_id)
    assert still.status == "pending"
    assert c.tool_client.calls == []

    # an eligible approver (not in the deny set) succeeds and executes
    ok = await c.proposal_service.decide(tenant_id=TENANT_A, proposal_id=prop.proposal_id,
                                         actor_sub="u-eligible", action="approve")
    assert ok.status == "approved"
    assert len(c.tool_client.calls) == 1


async def test_eligibility_checks_the_registered_approve_action_and_workspace():
    """Regression test: _check_eligibility previously called authz.allow with
    action="proposal.apply" — not a canonical <service>.<resource>.<verb>
    action (no such verb exists) and never registered in the rbac catalog, so
    OPA's action_known check ALWAYS denied it and no persona could approve any
    proposal with an affected URN, platform-wide. The real action is
    ai.proposal.approve (workspace-scoped in the rbac catalog), which every
    persona's grants + the UI's approveProposal gate already reference. This
    pins both the action string AND that the proposal's workspace (recorded
    in its WriteIntent args) is threaded through, since a workspace-scoped
    action with no workspace_id is denied by OPA's context check."""

    class RecordingAuthz:
        def __init__(self):
            self.calls = []

        async def allow(self, *, subject, action, tenant, resource_urn=None,
                        workspace_id=None):
            self.calls.append({"action": action, "resource_urn": resource_urn,
                               "workspace_id": workspace_id})
            return True

    authz = RecordingAuthz()
    c = build_container(make_settings(), mode="memory", authz=authz)
    run = await _run(c)
    intent = _intent()
    intent.args = {**intent.args, "workspace_id": "ws-77"}
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=intent, obo_user="u-77", auto_execute_policy={})

    decided = await c.proposal_service.decide(
        tenant_id=TENANT_A, proposal_id=prop.proposal_id, actor_sub="u-super",
        action="approve")

    assert decided.status == "approved"
    assert authz.calls  # the eligibility check actually ran
    for call in authz.calls:
        assert call["action"] == "ai.proposal.approve"
        assert call["workspace_id"] == "ws-77"


def _gated_intent():
    """A WriteIntent that declares the rbac action its write requires, so the
    propose-time caller-gate (ART-FR-044) evaluates it against the invoker."""
    i = _intent()
    i.required_action = "case.case.update"
    i.args = {**i.args, "workspace_id": "ws-77"}
    return i


async def test_caller_gate_blocks_proposal_when_invoker_unauthorized():
    """Permission-aware on-behalf-of: if the INVOKING caller cannot perform the
    write's required action, the copilot must not create the proposal (and must
    not auto-execute). No proposal row, no tool call."""
    from app.adapters.authz import DenyURNAuthz
    from app.domain.errors import PermissionDenied

    urn = f"wr:{TENANT_A}:case:case/c-91"
    # u-77 (the invoker) is denied case.case.update on the affected URN.
    c = build_container(make_settings(), mode="memory",
                        authz=DenyURNAuthz(denied={("u-77", urn)}))
    run = await _run(c)
    # even with an auto-execute policy, the gate fires first — nothing runs.
    policy = {"case-triage": {"write-proposal": {"reversible": "auto"}}}
    with pytest.raises(PermissionDenied):
        await c.proposal_service.create_from_intent(
            run=run, intent=_gated_intent(), obo_user="u-77", auto_execute_policy=policy)
    assert c.bus.of_type("proposal.created") == []
    assert c.tool_client.calls == []


async def test_caller_gate_allows_proposal_when_invoker_authorized():
    """When the invoker holds the required action, the proposal is created and
    the gate checked the CALLER's subject against the declared action + workspace."""
    class RecordingAuthz:
        def __init__(self):
            self.calls = []

        async def allow(self, *, subject, action, tenant, resource_urn=None,
                        workspace_id=None):
            self.calls.append({"subject": subject, "action": action,
                               "workspace_id": workspace_id})
            return True

    authz = RecordingAuthz()
    c = build_container(make_settings(), mode="memory", authz=authz)
    run = await _run(c)
    prop, executed = await c.proposal_service.create_from_intent(
        run=run, intent=_gated_intent(), obo_user="u-77", auto_execute_policy={})
    assert prop.status == "pending" and executed is False
    gate = [x for x in authz.calls if x["action"] == "case.case.update"]
    assert gate and gate[0]["subject"] == {"type": "user", "id": "u-77"}
    assert gate[0]["workspace_id"] == "ws-77"


async def test_caller_gate_skipped_for_autonomous_run():
    """An autonomous run (no invoking user) has no caller to bind to, so the
    gate is skipped — required_action alone must not block a system agent."""
    class DenyAllAuthz:
        async def allow(self, **_kw):
            return False

    c = build_container(make_settings(), mode="memory", authz=DenyAllAuthz())
    run = await _run(c)
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=_gated_intent(), obo_user=None, auto_execute_policy={})
    assert prop.status == "pending"


# ---- BRD 53 PA-FR-030: agent-scoped tool-allowlist enforcement (the new
#      load-bearing guardrail — an agent can only propose a tool ON ITS OWN
#      declared allow-list; this is what makes a custom agent's envelope real) --

from app.domain.entities import AgentVersion  # noqa: E402
from app.domain.errors import GuardrailViolation  # noqa: E402


async def _seed_agent(c, agent_key, tool_ids):
    """Persist a published agent version whose toolset is the allow-list."""
    from app.domain.entities import AgentDefinition
    await c.store.upsert_agent_definition(AgentDefinition(
        agent_key=agent_key, display_name=agent_key, description="",
        owner_team="t", default_write_mode="proposal", status="published"))
    await c.store.create_agent_version(AgentVersion(
        agent_key=agent_key, version=1, graph_ref="persona_copilot.v1",
        graph_digest="x", toolset=[{"tool_id": t} for t in tool_ids],
        status="published"))


async def test_guardrail_blocks_tool_outside_agent_allowlist():
    c = _container()
    # A custom agent allowed ONLY case.apply_disposition tries to propose a
    # promotion tool it never declared — fail closed, no proposal, no execution.
    await _seed_agent(c, "cust-x-copilot", ["case.apply_disposition"])
    run = await _run(c, agent="cust-x-copilot")
    off_list = WriteIntent(
        tool_id="experiment.model.promote", tool_version="1.0.0", tier="write-proposal",
        side_effects="reversible", args={"model_id": "m", "version": 1},
        rationale="should be blocked", affected_urns=[f"wr:{TENANT_A}:x:y/1"],
        predicted_effect={"summary": "x", "reversibility": "reversible", "blast_radius": 1})
    with pytest.raises(GuardrailViolation):
        await c.proposal_service.create_from_intent(
            run=run, intent=off_list, obo_user="u-77", auto_execute_policy={})
    assert c.bus.of_type("proposal.created") == []
    assert c.tool_client.calls == []


async def test_guardrail_allows_tool_on_agent_allowlist():
    c = _container()
    await _seed_agent(c, "cust-x-copilot", ["case.apply_disposition"])
    run = await _run(c, agent="cust-x-copilot")
    prop, _ = await c.proposal_service.create_from_intent(
        run=run, intent=_intent(), obo_user="u-77", auto_execute_policy={})
    assert prop.status == "pending"


async def test_guardrail_blocks_tier_above_ceiling():
    c = _container()
    await _seed_agent(c, "cust-x-copilot", ["case.apply_disposition"])
    run = await _run(c, agent="cust-x-copilot")
    too_high = WriteIntent(
        tool_id="case.apply_disposition", tool_version="1.0.0", tier="write-direct",
        side_effects="reversible", args={"case_id": "c-91"}, rationale="x",
        affected_urns=[f"wr:{TENANT_A}:case:case/c-91"],
        predicted_effect={"summary": "x", "reversibility": "reversible", "blast_radius": 1})
    with pytest.raises(GuardrailViolation):
        await c.proposal_service.create_from_intent(
            run=run, intent=too_high, obo_user="u-77", auto_execute_policy={})
