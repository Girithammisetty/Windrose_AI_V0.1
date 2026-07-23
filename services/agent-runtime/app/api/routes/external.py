"""External-agent governed write ingress (BRD 60 WS1).

The single seam that lets a customer's OWN agent — a LangGraph bot, a Copilot,
Claude, anything — submit a write that is forced through the exact same
four-eyes + WORM rails an internal Datacern agent uses. The external agent
authenticates with an AGENT principal (``typ=agent_obo`` or
``typ=agent_autonomous`` — a registered agent identity, never a raw user
token) and POSTs a proposed write; this route turns it into a ``WriteIntent``
and routes it through ``ProposalService.create_from_intent``.

Crucially it passes an EMPTY auto-execute policy, so an external agent's write
can ONLY ever become a *pending* proposal — never an inline write, regardless
of the tenant's auto-execute config. External callers are strictly less
trusted than the platform's own graphs, so the auto-execute fast-path is
denied to them entirely; a human must approve every external write in the
existing ``/inbox`` four-eyes queue.

Every downstream control applies UNCHANGED: the agent's declared
``AgentVersion.toolset`` allow-list + the ``write-proposal`` tier ceiling
(``_enforce_guardrail``), the on-behalf-of caller-permission gate
(``_authorize_caller`` — for workspace-scoped actions this already enforces
workspace containment via the per-resource RBAC grant), the server-derived
``predicted_effect`` (anti-laundering: the agent's own claim is demoted to
``agent_summary``), and the ``ai.proposal.v1`` WORM emit carrying
``via_agent`` (which agent acted) distinct from ``actor`` (on whose behalf).
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

from app.api.auth import principal_of
from app.api.schemas import proposal_view
from app.domain.entities import Run, new_uuid
from app.domain.errors import GuardrailViolation, PermissionDenied, ValidationFailed
from app.graphs.base import WriteIntent

router = APIRouter(prefix="/external/v1")

_REQUIRED_FIELDS = ("tool_id", "tool_version", "tier", "side_effects",
                    "args", "affected_urns")


@router.post("/intents")
async def submit_external_intent(request: Request, body: dict = Body(...)):
    """A registered external agent proposes a write. Always propose-only; the
    write only executes after a distinct human approves it (four-eyes)."""
    principal = await principal_of(request)

    # The caller MUST be an agent principal, never a raw user: this endpoint is
    # for an external AGENT acting under its own governed identity. A user
    # driving the platform uses the normal chat/graph path.
    if not principal.typ.startswith("agent"):
        raise PermissionDenied(
            "external intent ingress requires an agent principal "
            "(typ=agent_obo or agent_autonomous), not a user token")
    if not principal.agent_id or not principal.agent_version:
        raise PermissionDenied(
            "agent token must carry agent_id and agent_version")

    missing = [f for f in _REQUIRED_FIELDS if body.get(f) in (None, "")]
    if missing:
        raise ValidationFailed(f"missing required fields: {', '.join(missing)}")
    if not isinstance(body["args"], dict):
        raise ValidationFailed("args must be an object")
    affected = body["affected_urns"]
    if not isinstance(affected, list) or not affected:
        raise ValidationFailed("affected_urns must be a non-empty array")

    c = request.app.state.container

    try:
        agent_version = int(principal.agent_version)
    except (TypeError, ValueError) as exc:
        raise PermissionDenied("agent token agent_version must be an integer") from exc

    # External agents are strictly LESS trusted than the platform's own graphs,
    # so a declared toolset allow-list is MANDATORY for them: an external agent
    # may only ever propose a tool it has explicitly registered. This is the
    # deliberate asymmetry with internal agents — where a missing/empty toolset
    # means "no write surface declared" and downstream gates suffice — because
    # for an external caller an empty allow-list must mean DENY-ALL, not
    # allow-all. Without this gate an unregistered (or toolset-less) external
    # identity would sail past `_enforce_guardrail`'s allow-list check, which is
    # skipped on an empty set (services/agent-runtime/app/proposals/service.py),
    # leaving the external write surface open by omission. Fail closed here,
    # before any run/proposal row exists (BRD 60 allow-list defense-in-depth).
    version = await c.store.get_agent_version(principal.agent_id, agent_version)
    declared = [t.get("tool_id") for t in (version.toolset if version else [])
                if isinstance(t, dict) and t.get("tool_id")]
    if not declared:
        raise GuardrailViolation(
            f"external agent {principal.agent_id!r} v{agent_version} has no "
            "registered toolset allow-list; register the agent's permitted tools "
            "before it can propose a write")

    # obo_sub present -> the agent acts on behalf of a real user, whose
    # per-resource grants scope its reach (the caller-gate binds). Absent ->
    # an autonomous external agent, which relies entirely on distinct-approver
    # four-eyes (no self-approval, ever) for its writes.
    obo_user = principal.obo_sub
    principal_type = "user_obo" if obo_user else "agent_autonomous"

    run = Run(
        run_id=new_uuid(),
        tenant_id=principal.tenant_id,
        # No graph session backs an external ingest; a fresh session id keeps
        # the run row shape valid (runs.session_id is NOT NULL, no FK).
        session_id=body.get("session_id") or new_uuid(),
        agent_key=principal.agent_id,
        agent_version=agent_version,
        temporal_workflow_id=None,
        status="external_intent",
        principal_type=principal_type,
        obo_sub=obo_user,
    )
    await c.store.create_run(run)

    intent = WriteIntent(
        tool_id=body["tool_id"],
        tool_version=body["tool_version"],
        tier=body["tier"],
        side_effects=body["side_effects"],
        args=body["args"],
        rationale=body.get("rationale", ""),
        affected_urns=list(affected),
        # The agent's own effect claim; derive_effect recomputes the ground
        # truth server-side and demotes this to agent_summary (anti-laundering).
        predicted_effect=body.get("predicted_effect") or {},
        required_action=body.get("required_action"),
        workspace_id=body.get("workspace_id"),
    )

    # Empty auto-execute policy => is_auto_execute() is always False =>
    # propose-only. Governance stance for external callers (see module docstring).
    prop, executed = await c.proposal_service.create_from_intent(
        run=run, intent=intent, obo_user=obo_user, auto_execute_policy={})

    return {"data": proposal_view(prop), "executed": executed}
