"""Replay / no-side-effect executor (ART-FR-015).

``POST /api/v1/replay`` runs a published agent graph for a given case/inputs in
REPLAY mode and returns the candidate output the agent WOULD have produced —
WITHOUT any side effect: no Run/Session/Proposal rows, no case mutation, no
events. The graph's WriteIntent is captured and returned as data; RAG grounding
is pinned to the requested ``memory_snapshot_ver`` for determinism.

Consumer: eval-service's ``AgentRuntimeReplayProvider`` (EVL-FR-020) scores live
candidates against this endpoint. Contract (request body / response) is built to
match that client exactly.

AuthZ: reuses the existing canonical action ``ai.agent_session.execute`` (replay
is a read-only agent execute). A principal holding that action (or ``*``) in its
token scopes is allowed directly — the platform's scope_ok rule for service /
agent principals (eval calls as ``svc:eval-service``); user principals fall back
to the OPA projection. No new rbac action is required.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

from app.api.auth import principal_of
from app.domain.errors import PermissionDenied, ValidationFailed

router = APIRouter(prefix="/api/v1")

# Replay is a read-only agent execute; reuse the registered canonical action
# rather than minting rbac churn (ai.agent_session VerbExecute already exists).
REPLAY_ACTION = "ai.agent_session.execute"


async def _authorize_replay(principal, container) -> None:
    scopes = principal.scopes or []
    if REPLAY_ACTION in scopes or "*" in scopes:
        return
    allowed = await container.authz.allow(
        subject={"type": principal.typ, "id": principal.sub},
        action=REPLAY_ACTION, tenant=principal.tenant_id, resource_urn=None)
    if not allowed:
        raise PermissionDenied(f"replay requires {REPLAY_ACTION}")


def _normalise_inputs(body: dict, tenant_id: str) -> dict:
    """Map eval's ``input`` (an eval-case input) to graph state. tenant_id ALWAYS
    comes from the verified token, never the body (BR-11)."""
    raw = dict(body.get("input") or {})
    inputs: dict = {**raw, "tenant_id": tenant_id}
    # case-triage needs a case_id in state; accept it directly or via a case URN /
    # chat-style metadata so eval cases can be shaped like a normal chat request.
    if "case_id" not in inputs:
        meta = raw.get("metadata") or {}
        ctx = meta.get("context_urn") or raw.get("context_urn") or ""
        if meta.get("case_id"):
            inputs["case_id"] = meta["case_id"]
        elif isinstance(ctx, str) and ":case/" in ctx:
            inputs["case_id"] = ctx.split(":case/")[-1]
    if "query" not in inputs:
        msgs = raw.get("messages") or []
        user_msgs = [m.get("content", "") for m in msgs if m.get("role") == "user"]
        if user_msgs:
            inputs["query"] = user_msgs[-1]
    return inputs


def _intent_dict(wi) -> dict:
    return {"tool_id": wi.tool_id, "tool_version": wi.tool_version, "tier": wi.tier,
            "side_effects": wi.side_effects, "args": wi.args, "rationale": wi.rationale,
            "affected_urns": wi.affected_urns, "predicted_effect": wi.predicted_effect}


@router.post("/replay")
async def replay(request: Request, body: dict = Body(...)):
    principal = await principal_of(request)
    c = request.app.state.container
    await _authorize_replay(principal, c)

    agent_key = body.get("agent_key")
    if not agent_key:
        raise ValidationFailed("agent_key required")
    tenant_id = principal.tenant_id
    inputs = _normalise_inputs(body, tenant_id)
    if agent_key == "case-triage" and "case_id" not in inputs:
        raise ValidationFailed("case-triage replay requires input.case_id or a case context_urn")

    version = await c.store.latest_published_version(agent_key)
    if version is None:
        raise ValidationFailed(f"agent {agent_key} has no published version")

    cfg = await c.store.get_tenant_config(tenant_id, agent_key)
    prompt_params = cfg.prompt_params if cfg else {}

    # Real downstream identity for the read-only graph (case + memory + ai-gateway):
    # an agent-autonomous token for the replayed agent, scoped like a normal run.
    obo_token = c.token_minter.mint_agent_autonomous(
        tenant_id=tenant_id, agent_key=agent_key, agent_version=version, scopes=["*"])

    snapshot_ver = body.get("memory_snapshot_ver")
    outcome = await c.run_engine.replay(
        agent_key=agent_key, inputs=inputs, obo_token=obo_token,
        prompt_params=prompt_params, memory_snapshot_ver=snapshot_ver)

    output = {
        "agent_key": agent_key,
        "agent_version": version,
        "answer": outcome.final_text,
        "content": outcome.final_text,
        "disposition": outcome.structured or None,
        "structured": outcome.structured,
        "evidence": outcome.evidence,
        # WriteIntents captured-not-executed (no proposal was created).
        "write_intents": ([_intent_dict(outcome.write_intent)]
                          if outcome.write_intent is not None else []),
        "usage": outcome.usage,
        "trace": outcome.trace,
        "memory_snapshot_ver": snapshot_ver,
        "no_side_effect": True,
    }
    return {"output": output, "no_side_effect": True}
