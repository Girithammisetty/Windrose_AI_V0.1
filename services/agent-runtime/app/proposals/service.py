"""Proposal framework + HITL (ART-FR-041..046) — the core of the product.

A write-tier WriteIntent becomes a Proposal (tool, args, rationale, affected URNs,
predicted effect) awaiting a durable human decision. On APPROVE / EDIT_APPROVE the
service issues the SIGNED proposal-execution grant (matching tool-plane's contract)
and executes the tool; on REJECT/RESPOND nothing executes. Decisions are
first-wins (BR-12), approver-eligibility gated by OPA (ART-FR-044), and every
transition emits ``ai.proposal.v1`` (ART-FR-046).
"""

from __future__ import annotations

from datetime import UTC

from app.constants import TOPIC_PROPOSAL
from app.domain import policy as policy_mod
from app.domain.canonical import args_digest as compute_digest
from app.domain.entities import Proposal, new_uuid, now
from app.domain.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ProposalExpired,
    ValidationFailed,
)
from app.domain.urn import proposal_urn
from app.events.envelope import make_envelope
from app.graphs.base import WriteIntent


class ProposalService:
    def __init__(self, *, store, authz, grant_issuer, token_minter, tool_client, bus,
                 realtime, settings, transcripts=None) -> None:
        self._store = store
        self._authz = authz
        self._grants = grant_issuer
        self._tokens = token_minter
        self._tools = tool_client
        self._bus = bus
        self._rt = realtime
        self._settings = settings
        # SLM distillation milestone 1: join the human decision onto the run's
        # transcript (the training signal). None → off.
        self._transcripts = transcripts

    # ---- creation (ART-FR-042) ---------------------------------------------
    async def create_from_intent(
        self, *, run, intent: WriteIntent, obo_user: str | None,
        auto_execute_policy: dict,
    ) -> tuple[Proposal, bool]:
        """Create a Proposal from a graph WriteIntent. Returns (proposal, executed).
        If tenant auto-execute policy resolves to ``auto`` for this cell, the
        proposal is executed immediately with ``decision.actor = policy:auto``."""
        # Permission-aware on-behalf-of gate (ART-FR-044): the copilot operates
        # AS the invoking user, so it must not propose — let alone auto-execute —
        # a write the caller could not perform themselves. When the graph declares
        # the write's required rbac action, enforce it against the caller on every
        # affected URN BEFORE any proposal row exists. (Graphs that declare a
        # workspace-scoped action must carry workspace_id in the intent args, as
        # triage does; None action = no gate, e.g. autonomous runs.)
        if intent.required_action and obo_user:
            await self._authorize_caller(
                intent, obo_user=obo_user, tenant_id=run.tenant_id)
        pid = new_uuid()
        expires = now().timestamp() + self._settings.proposal_default_ttl_seconds
        from datetime import datetime
        prop = Proposal(
            proposal_id=pid, tenant_id=run.tenant_id, session_id=run.session_id,
            run_id=run.run_id, agent_key=run.agent_key, agent_version=run.agent_version,
            obo_user=obo_user, tool_id=intent.tool_id, tool_version=intent.tool_version,
            tier=intent.tier, side_effects=intent.side_effects, args=intent.args,
            rationale=intent.rationale, affected_urns=intent.affected_urns,
            predicted_effect=intent.predicted_effect,
            expires_at=datetime.fromtimestamp(expires, tz=UTC), status="pending")
        await self._store.create_proposal(prop)
        await self._store.supersede_pending(
            tenant_id=run.tenant_id, run_id=run.run_id, tool_id=intent.tool_id,
            urns=intent.affected_urns, except_id=pid)
        await self._emit(prop, "proposal.created", decision=None)
        await self._rt.publish(topic=f"agent_run:{run.run_id}", event="proposal_created",
                               data={"proposal_id": pid, "tool_id": intent.tool_id},
                               tenant_id=run.tenant_id)

        auto = policy_mod.is_auto_execute(
            auto_execute_policy, run.agent_key, intent.tier, intent.side_effects)
        if auto:
            decided = await self._store.decide_proposal(
                tenant_id=run.tenant_id, proposal_id=pid, new_status="approved",
                decision={"actor": "policy:auto", "action": "approve",
                          "decided_at": now().isoformat()}, decided_at=now())
            if decided:
                await self._execute(decided, decided_by="policy:auto",
                                    obo_user=obo_user, args=decided.args)
                await self._emit(decided, "proposal.approved", decision=decided.decision)
                return decided, True
        return prop, False

    async def _authorize_caller(self, intent: WriteIntent, *, obo_user: str,
                                tenant_id: str) -> None:
        """Enforce the invoking caller holds ``intent.required_action`` on every
        affected URN (permission-aware, on-behalf-of). Denial raises
        PermissionDenied so no proposal is created and nothing auto-executes —
        the copilot cannot escalate a user's privileges by proposing on their
        behalf. Uses the same OPA engine + rbac projection the UI is gated by."""
        subject = {"type": "user", "id": obo_user}
        workspace_id = intent.args.get("workspace_id")
        for urn in intent.affected_urns:
            allowed = await self._authz.allow(
                subject=subject, action=intent.required_action, tenant=tenant_id,
                resource_urn=urn, workspace_id=workspace_id)
            if not allowed:
                raise PermissionDenied(
                    f"caller lacks {intent.required_action} on {urn}: the copilot "
                    "cannot propose an action the invoker cannot perform")

    # ---- decision (ART-FR-042/044, BR-8/BR-12) -----------------------------
    async def decide(
        self, *, tenant_id: str, proposal_id: str, actor_sub: str, action: str,
        message: str | None = None, edited_args: dict | None = None,
        self_approval_allowed: bool = False, execute: bool = True,
    ) -> Proposal:
        prop = await self._store.get_proposal(tenant_id, proposal_id)
        if prop is None:
            raise NotFound("proposal not found")
        if prop.status != "pending":
            raise Conflict("proposal already decided",
                           details={"winning_decision": prop.decision})
        if prop.expires_at <= now():
            raise ProposalExpired("proposal expired")

        if action in ("approve", "edit_args"):
            await self._check_eligibility(prop, actor_sub, self_approval_allowed)

        exec_args = prop.args
        decision: dict = {"actor": f"user:{actor_sub}", "action": action,
                          "decided_at": now().isoformat()}
        if message:
            decision["message"] = message

        if action == "edit_args":
            if not isinstance(edited_args, dict) or not edited_args:
                raise ValidationFailed("edit_args requires edited_args")
            decision["diff"] = _diff(prop.args, edited_args)
            decision["edited_args"] = edited_args
            exec_args = edited_args
            new_status = "edited_approved"
        elif action == "approve":
            new_status = "approved"
        elif action == "reject":
            new_status = "rejected"
        elif action == "respond":
            # free-text guidance, no execution, proposal stays actionable for the
            # agent but is terminal as a proposal record (cancelled path).
            new_status = "cancelled"
        else:
            raise ValidationFailed(f"unknown action {action!r}")

        decided = await self._store.decide_proposal(
            tenant_id=tenant_id, proposal_id=proposal_id, new_status=new_status,
            decision=decision, decided_at=now())
        if decided is None:  # lost the race (BR-12)
            fresh = await self._store.get_proposal(tenant_id, proposal_id)
            raise Conflict("proposal already decided",
                           details={"winning_decision": fresh.decision if fresh else None})

        # In Temporal mode execution is deferred to the workflow (durable, retried,
        # idempotent); execute inline only when there is no backing workflow.
        if execute and new_status in ("approved", "edited_approved"):
            await self._execute(decided, decided_by=actor_sub, obo_user=decided.obo_user,
                                args=exec_args)

        # SLM distillation: join the human decision + any correction onto the
        # run's transcript (best-effort; an approved/edited proposal is a gold
        # (input -> corrected-output) training pair).
        if self._transcripts is not None:
            await self._transcripts.attach_decision(
                tenant_id=tenant_id, proposal_id=proposal_id, action=action,
                edited_args=edited_args, decided_by=actor_sub,
                decided_at=decided.updated_at)

        event = {"approved": "proposal.approved", "edited_approved": "proposal.edited_approved",
                 "rejected": "proposal.rejected", "cancelled": "proposal.cancelled"}[new_status]
        await self._emit(decided, event, decision=decision)
        return decided

    async def _check_eligibility(self, prop: Proposal, actor_sub: str,
                                 self_approval_allowed: bool) -> None:
        # Self-approval guard (ART-FR-044).
        if prop.obo_user and actor_sub == prop.obo_user and not self_approval_allowed:
            raise PermissionDenied("self-approval not permitted for this tenant")
        # Approver must hold ai.proposal.approve on EVERY affected URN. This was
        # previously "proposal.apply" — not a canonical <service>.<resource>.<verb>
        # action (no such verb exists; the real rbac catalog registers
        # ai.proposal with read/list/approve), so OPA's action_known check
        # ALWAYS denied it and NO persona could ever approve a proposal that
        # had at least one affected URN, platform-wide. ai.proposal.approve is
        # the action every persona's grants + the UI's approveProposal gate
        # already reference (rbac seed/roles_actions.yaml).
        # ai.proposal is workspace-scoped (RBC catalog wsScoped=true) — OPA's
        # ctx_ok denies a workspace-scoped action carrying no workspace, so
        # thread the proposal's workspace, recorded in its WriteIntent args at
        # creation time, through the same way every write-proposal tool does.
        subject = {"type": "user", "id": actor_sub}
        workspace_id = prop.args.get("workspace_id")
        for urn in prop.affected_urns:
            allowed = await self._authz.allow(
                subject=subject, action="ai.proposal.approve", tenant=prop.tenant_id,
                resource_urn=urn, workspace_id=workspace_id)
            if not allowed:
                raise PermissionDenied(f"approver lacks permission on {urn}")

    # ---- execution: issue signed grant + call tool-plane -------------------
    async def _execute(self, prop: Proposal, *, decided_by: str, obo_user: str | None,
                       args: dict):
        """Issue the RS256 signed grant bound to (tenant, tool, tier, args) and
        present it to tool-plane in ``params._meta.proposal_grant``."""
        grant = self._grants.issue(
            proposal_id=prop.proposal_id, tenant_id=prop.tenant_id, tool_id=prop.tool_id,
            tier=prop.tier, args=args, decided_by=decided_by)
        # OBO token for tool-plane authN; scope carries the tool id so the toolset
        # gate passes, and obo_sub drives case-service dual attribution.
        token = self._tokens.mint_agent_obo(
            tenant_id=prop.tenant_id, obo_sub=obo_user or decided_by,
            agent_key=prop.agent_key, agent_version=prop.agent_version,
            workspace_id=None, scopes=[prop.tool_id])
        result = await self._tools.call(
            tool_id=prop.tool_id, arguments=args, tenant_id=prop.tenant_id,
            auth_token=token, version=prop.tool_version, proposal_grant=grant)
        await self._rt.publish(
            topic=f"agent_run:{prop.run_id}", event="tool_call_result",
            data={"tool_id": prop.tool_id, "ok": result.ok, "status": result.status},
            tenant_id=prop.tenant_id)
        return result

    # ---- events ------------------------------------------------------------
    async def _emit(self, prop: Proposal, event_type: str, *, decision: dict | None) -> None:
        payload = {"proposal_id": prop.proposal_id, "agent_key": prop.agent_key,
                   "agent_version": prop.agent_version, "tool_id": prop.tool_id,
                   "affected_urns": prop.affected_urns}
        if decision:
            payload["decision"] = decision
        actor = {"type": "agent", "id": prop.agent_key}
        if decision and decision.get("actor", "").startswith("user:"):
            actor = {"type": "user", "id": decision["actor"].split(":", 1)[1]}
        env = make_envelope(
            event_type=event_type, tenant_id=prop.tenant_id, actor=actor,
            via_agent={"agent_id": prop.agent_key, "version": str(prop.agent_version)},
            resource_urn=proposal_urn(prop.tenant_id, prop.proposal_id), payload=payload)
        await self._store.enqueue_outbox(tenant_id=prop.tenant_id, topic=TOPIC_PROPOSAL,
                                         envelope=env)
        await self._bus.publish(TOPIC_PROPOSAL, env)

    # exposed for the Temporal execute activity / non-temporal inline path
    async def execute_approved(self, prop: Proposal, *, decided_by: str, args: dict):
        return await self._execute(prop, decided_by=decided_by, obo_user=prop.obo_user,
                                   args=args)


def _diff(before: dict, after: dict) -> list[dict]:
    out = []
    for k in sorted(set(before) | set(after)):
        if before.get(k) != after.get(k):
            out.append({"field": k, "from": before.get(k), "to": after.get(k)})
    return out


def digest_for(args: dict) -> str:
    return compute_digest(args)
