"""agent-registry APIs (ART-FR-001..005, 060..063, 073) + kill switches."""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

from app.api.auth import is_operator, principal_of
from app.domain import policy as policy_mod
from app.domain.entities import (
    AgentDefinition,
    AgentVersion,
    KillSwitch,
    Rollout,
    TenantAgentConfig,
    new_uuid,
)
from app.domain.errors import Conflict, EvalGateFailed, NotFound, PermissionDenied
from app.graphs.base import graph_digest
from app.signing import build_card, sign_card

router = APIRouter(prefix="/api/v1/registry")


def _require_operator(principal):
    if not is_operator(principal):
        raise PermissionDenied("operator scope required")


async def _has_agent_cap(request: Request, principal, action: str) -> bool:
    """True if the caller may perform a tenant agent-admin action — resolved from
    the caller's rbac CAPABILITIES via OPA (the same engine the UI is gated by),
    not a raw JWT scope. Platform operators always pass. This is what lets a
    tenant-defined CUSTOM role (carrying ai.agent.read/admin) unlock the
    agent-admin surfaces, instead of only the built-in Admin role."""
    if is_operator(principal):
        return True
    c = request.app.state.container
    return await c.authz.allow(
        subject=principal.actor, action=action, tenant=principal.tenant_id)


async def _require_agent_cap(request: Request, principal, action: str) -> None:
    if not await _has_agent_cap(request, principal, action):
        raise PermissionDenied(f"{action} capability required")


# ---- Tier 2b: catalog read views (browse surface for the admin UI) ----------

def _definition_view(d: AgentDefinition, latest_published: int | None) -> dict:
    return {"agent_key": d.agent_key, "display_name": d.display_name,
            "description": d.description, "owner_team": d.owner_team,
            "default_write_mode": d.default_write_mode, "status": d.status,
            "latest_published_version": latest_published}


def _version_view(v: AgentVersion) -> dict:
    return {"agent_key": v.agent_key, "version": v.version, "status": v.status,
            "graph_ref": v.graph_ref, "graph_digest": v.graph_digest,
            "guardrail_profile": v.guardrail_profile,
            "eval_gate_result_id": v.eval_gate_result_id,
            "toolset": v.toolset, "model_config": v.model_config}


def _tenant_config_view(agent_key: str, cfg: TenantAgentConfig | None) -> dict:
    if cfg is None:
        # No row yet: report the runtime's real defaults (Orchestrator treats a
        # missing config as enabled/unpinned) with configured=false so the UI
        # is honest about "never explicitly configured".
        return {"agent_key": agent_key, "configured": False, "enabled": True,
                "pinned_version": None, "prompt_params": {},
                "auto_execute_policy": {}, "self_approval": False}
    return {"agent_key": agent_key, "configured": True, "enabled": cfg.enabled,
            "pinned_version": cfg.pinned_version, "prompt_params": cfg.prompt_params,
            "auto_execute_policy": cfg.auto_execute_policy,
            "self_approval": cfg.self_approval}


@router.get("/agents")
async def list_agents(request: Request):
    """Agent catalog browse (Tier 2b admin surface). Control-plane read —
    operator or tenant admin, same bar as the kill-switch list."""
    principal = await principal_of(request)
    await _require_agent_cap(request, principal, "ai.agent.read")
    c = request.app.state.container
    defs = await c.store.list_agent_definitions()
    out = []
    for d in sorted(defs, key=lambda x: x.agent_key):
        # BRD 53 isolation: platform agents (owner_tenant NULL) are visible to
        # all; a tenant custom agent is visible only within its owning tenant.
        if d.owner_tenant is not None and d.owner_tenant != principal.tenant_id \
                and not is_operator(principal):
            continue
        latest = await c.store.latest_published_version(d.agent_key)
        out.append(_definition_view(d, latest))
    return {"data": out}


@router.get("/agents/{agent_key}/versions")
async def list_versions(request: Request, agent_key: str):
    """Versions of one agent, newest first (Tier 2b admin surface)."""
    principal = await principal_of(request)
    await _require_agent_cap(request, principal, "ai.agent.read")
    c = request.app.state.container
    if await c.store.get_agent_definition(agent_key) is None:
        raise NotFound(f"agent {agent_key} not defined")
    versions = await c.store.list_agent_versions(agent_key)
    versions.sort(key=lambda v: v.version, reverse=True)
    return {"data": [_version_view(v) for v in versions]}


@router.post("/agents")
async def create_agent(request: Request, body: dict = Body(...)):
    principal = await principal_of(request)
    _require_operator(principal)
    c = request.app.state.container
    d = AgentDefinition(
        agent_key=body["agent_key"], display_name=body["display_name"],
        description=body.get("description", ""), owner_team=body.get("owner_team", "platform-ai"),
        default_write_mode=body.get("default_write_mode", "proposal"), status="draft")
    await c.store.upsert_agent_definition(d)
    return {"data": {"agent_key": d.agent_key, "status": d.status}}


@router.post("/agents/{agent_key}/versions")
async def create_version(request: Request, agent_key: str, body: dict = Body(...)):
    principal = await principal_of(request)
    _require_operator(principal)
    c = request.app.state.container
    if await c.store.get_agent_definition(agent_key) is None:
        raise NotFound(f"agent {agent_key} not defined")
    version = int(body["version"])
    if await c.store.get_agent_version(agent_key, version) is not None:
        raise Conflict("version already exists")
    graph_ref = body["graph_ref"]
    v = AgentVersion(
        agent_key=agent_key, version=version, graph_ref=graph_ref,
        graph_digest=graph_digest(graph_ref), prompt_refs=body.get("prompt_refs", []),
        toolset=body.get("toolset", []), model_config=body.get("model_config", {}),
        eval_gate=body.get("eval_gate", {}), eval_gate_result_id=body.get("eval_gate_result_id"),
        status="draft")
    await c.store.create_agent_version(v)
    return {"data": {"agent_key": agent_key, "version": version, "status": "draft"}}


@router.post("/agents/{agent_key}/versions/{version}/publish")
async def publish_version(request: Request, agent_key: str, version: int,
                          body: dict = Body(default={})):
    principal = await principal_of(request)
    _require_operator(principal)
    c = request.app.state.container
    v = await c.store.get_agent_version(agent_key, version)
    if v is None:
        raise NotFound("version not found")
    # Publish gate (ART-FR-060, AC-8): a passing eval gate result is required
    # unless an operator force is supplied with a reason.
    if not v.eval_gate_result_id and not body.get("force"):
        raise EvalGateFailed("no passing eval-gate result attached to this version")
    if body.get("force") and not body.get("reason"):
        raise EvalGateFailed("force publish requires a reason")
    d = await c.store.get_agent_definition(agent_key)
    card = build_card(agent_key=agent_key, version=version, display_name=d.display_name,
                      description=d.description, write_mode=d.default_write_mode,
                      skills=[], endpoint=f"https://agent-runtime.internal/a2a/{agent_key}",
                      eval_score_ref=v.eval_gate_result_id)
    sig = sign_card(c.signing_key, card)
    card["signature"] = {"alg": "RS256", "kid": c.signing_key.kid, "value": sig}
    v.a2a_card = card
    v.card_signature = sig
    v.principal_ref = v.principal_ref or f"spiffe://windrose/ns/ai/agent/{agent_key}"
    v.status = "published"
    await c.store.update_agent_version(v)
    return {"data": {"agent_key": agent_key, "version": version, "status": "published"}}


@router.get("/tenants/self/agents/{agent_key}")
async def get_tenant_agent_config(request: Request, agent_key: str):
    """The caller-tenant's config for one agent (Tier 2b read side of the PUT
    below). Tenant admin — it's the same control surface the PUT guards."""
    principal = await principal_of(request)
    await _require_agent_cap(request, principal, "ai.agent.read")
    c = request.app.state.container
    if await c.store.get_agent_definition(agent_key) is None:
        raise NotFound(f"agent {agent_key} not defined")
    cfg = await c.store.get_tenant_config(principal.tenant_id, agent_key)
    return {"data": _tenant_config_view(agent_key, cfg)}


@router.put("/tenants/self/agents/{agent_key}")
async def put_tenant_config(request: Request, agent_key: str, body: dict = Body(...)):
    principal = await principal_of(request)
    await _require_agent_cap(request, principal, "ai.agent.admin")
    c = request.app.state.container
    policy = body.get("auto_execute_policy", {})
    policy_mod.validate_auto_policy(policy)  # 422 on destructive/admin auto (AC-5)
    cfg = TenantAgentConfig(
        tenant_id=principal.tenant_id, agent_key=agent_key, enabled=body.get("enabled", True),
        pinned_version=body.get("pinned_version"), prompt_params=body.get("prompt_params", {}),
        auto_execute_policy=policy, self_approval=body.get("self_approval", False))
    await c.store.put_tenant_config(cfg)
    return {"data": {"agent_key": agent_key, "enabled": cfg.enabled,
                     "pinned_version": cfg.pinned_version}}


# ---- BRD 53: tenant-authored CUSTOM agents (config over the shared graph) ----

import re as _re

# The ONLY graph a tenant custom agent may run on — the shared, platform-owned,
# eval-gated safe template. A tenant can never name any other graph_ref.
_CUSTOM_GRAPH_REF = "persona_copilot.v1"
# Custom agents cap at write-proposal; higher tiers are operator-fixed-agent only.
_TIER_RANK = {"read": 0, "write-proposal": 1, "write-direct": 2, "admin": 3}


@router.post("/tenants/self/agents")
async def create_custom_agent(request: Request, body: dict = Body(...)):
    """Create a TENANT custom agent as governed CONFIGURATION (BRD 53) — never
    code. The definition is scoped to the caller tenant (owner_tenant), forced
    onto the shared persona_copilot.v1 graph, and its allow-list becomes the
    AgentVersion.toolset that ProposalService enforces at runtime (PA-FR-030).
    Envelope validated here (PA-FR-060): non-empty allow-list, propose_tool ∈
    allow-list, tier ≤ write-proposal. Published immediately + enabled."""
    principal = await principal_of(request)
    await _require_agent_cap(request, principal, "ai.agent.admin")
    c = request.app.state.container
    tenant = principal.tenant_id

    name = str(body.get("display_name") or body.get("name") or "").strip()
    if not name:
        raise PermissionDenied("display_name is required")  # 4xx envelope error
    persona = str(body.get("persona") or "").strip()
    if not persona:
        raise EvalGateFailed("persona is required (an rbac role this agent serves)")
    allowed_tools = body.get("allowed_tools") or []
    if not isinstance(allowed_tools, list) or not allowed_tools:
        raise EvalGateFailed("allowed_tools must be a non-empty list of tool ids")
    allowed_tools = [str(t) for t in allowed_tools]
    propose_tool = body.get("propose_tool")
    if propose_tool is not None and propose_tool not in allowed_tools:
        raise EvalGateFailed(f"propose_tool {propose_tool!r} must be in allowed_tools")
    max_tier = str(body.get("max_tier") or "write-proposal")
    if _TIER_RANK.get(max_tier, 99) > _TIER_RANK["write-proposal"]:
        raise EvalGateFailed(
            f"max_tier {max_tier!r} exceeds the custom-agent ceiling (write-proposal)")
    # A tenant may NEVER name another graph_ref — it is forced to the shared one.
    if body.get("graph_ref") and body["graph_ref"] != _CUSTOM_GRAPH_REF:
        raise EvalGateFailed(
            f"custom agents run only on {_CUSTOM_GRAPH_REF} (graph_ref is not configurable)")

    slug = _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "agent"
    agent_key = f"cust-{str(tenant).replace('-', '')[:8]}-{slug}"
    if await c.store.get_agent_definition(agent_key) is not None:
        raise Conflict(f"a custom agent named {name!r} already exists")

    await c.store.upsert_agent_definition(AgentDefinition(
        agent_key=agent_key, display_name=name,
        description=body.get("description", f"Tenant custom copilot for {persona}"),
        owner_team=f"tenant:{tenant}", default_write_mode="proposal",
        status="published", owner_tenant=tenant))

    toolset = [{"tool_id": t, "version_range": ">=1.0.0"} for t in allowed_tools]
    card = build_card(agent_key=agent_key, version=1, display_name=name,
                      description=body.get("description", ""), write_mode="proposal",
                      skills=[], endpoint=f"https://agent-runtime.internal/a2a/{agent_key}",
                      eval_score_ref="persona-copilot-shared-gate")
    sig = sign_card(c.signing_key, card)
    card["signature"] = {"alg": "RS256", "kid": c.signing_key.kid, "value": sig}
    await c.store.create_agent_version(AgentVersion(
        agent_key=agent_key, version=1, graph_ref=_CUSTOM_GRAPH_REF,
        graph_digest=graph_digest(_CUSTOM_GRAPH_REF), toolset=toolset,
        model_config={"request_class": "chat", "max_rung": 1, "temperature": 0.2},
        memory_policy={"scopes_readable": ["workspace", "tenant"], "scopes_writable": []},
        # The SHARED graph is what carries the platform eval gate — every custom
        # agent reuses it, so a passing shared-gate marker satisfies publish.
        eval_gate={"suite_id": "persona-copilot-suite"},
        eval_gate_result_id="persona-copilot-shared-gate",
        a2a_card=card, card_signature=sig,
        principal_ref=f"spiffe://windrose/ns/ai/agent/{agent_key}", status="published"))

    # Enable it for the tenant and carry the persona/prompt/propose_tool that the
    # shared graph reads at runtime (prompt_params is the config channel).
    await c.store.put_tenant_config(TenantAgentConfig(
        tenant_id=tenant, agent_key=agent_key, enabled=True,
        prompt_params={"persona": persona,
                       "system_prompt": str(body.get("system_prompt") or "")[:2000],
                       "propose_tool": propose_tool},
        auto_execute_policy={}, self_approval=False))

    return {"data": {"agent_key": agent_key, "status": "published",
                     "graph_ref": _CUSTOM_GRAPH_REF, "allowed_tools": allowed_tools,
                     "persona": persona, "owner_tenant": tenant}}


@router.post("/rollouts")
async def create_rollout(request: Request, body: dict = Body(...)):
    principal = await principal_of(request)
    _require_operator(principal)
    c = request.app.state.container
    r = Rollout(
        rollout_id=new_uuid(), agent_key=body["agent_key"], cell=body.get("cell", c.settings.env),
        mode=body["mode"], candidate_version=int(body["candidate_version"]),
        baseline_version=int(body["baseline_version"]), pct=int(body.get("pct", 0)),
        tenant_filter=body.get("tenant_filter", {}), status="active")
    await c.store.create_rollout(r)
    return {"data": {"rollout_id": r.rollout_id, "status": r.status}}


@router.post("/rollouts/{rollout_id}/rollback")
async def rollback_rollout(request: Request, rollout_id: str):
    principal = await principal_of(request)
    _require_operator(principal)
    c = request.app.state.container
    r = await c.store.get_rollout(rollout_id)
    if r is None:
        raise NotFound("rollout not found")
    r.status = "rolled_back"
    await c.store.update_rollout(r)
    return {"data": {"rollout_id": rollout_id, "status": "rolled_back"}}


@router.post("/rollouts/{rollout_id}/promote")
async def promote_rollout(request: Request, rollout_id: str):
    principal = await principal_of(request)
    _require_operator(principal)
    c = request.app.state.container
    r = await c.store.get_rollout(rollout_id)
    if r is None:
        raise NotFound("rollout not found")
    r.status = "promoted"
    await c.store.update_rollout(r)
    return {"data": {"rollout_id": rollout_id, "status": "promoted"}}


@router.get("/kill-switches")
async def list_kills(request: Request):
    """List active kill switches (ART-FR-073 admin surface). Operators see every
    tenant's active kills; a tenant admin sees their own tenant's + any global
    (platform-wide) kill. Anyone else is denied — this is an operations/safety
    control-plane view, not a general read."""
    principal = await principal_of(request)
    await _require_agent_cap(request, principal, "ai.agent.read")
    c = request.app.state.container
    tenant_id = None if is_operator(principal) else principal.tenant_id
    kills = await c.store.list_kill_switches(tenant_id)
    return {"data": [
        {"kill_id": k.kill_id, "scope": k.scope, "agent_key": k.agent_key, "version": k.version,
         "tenant_id": k.tenant_id, "active": k.active, "reason": k.reason, "set_by": k.set_by,
         "created_at": k.created_at.isoformat() if k.created_at else None}
        for k in kills
    ]}


@router.post("/kill-switches")
async def create_kill(request: Request, body: dict = Body(...)):
    principal = await principal_of(request)
    scope = body.get("scope", "agent_version_tenant")
    tenant_scoped = scope == "agent_version_tenant"
    if not (is_operator(principal)
            or (tenant_scoped and await _has_agent_cap(request, principal, "ai.agent.admin"))):
        raise PermissionDenied("operator (or ai.agent.admin for own-tenant scope) required")
    if not body.get("reason"):
        raise EvalGateFailed("reason required")
    c = request.app.state.container
    ks = KillSwitch(
        kill_id=new_uuid(), scope=scope, agent_key=body["agent_key"],
        version=body.get("version"),
        tenant_id=(principal.tenant_id if tenant_scoped else body.get("tenant_id")),
        active=True, reason=body["reason"], set_by=principal.sub)
    await c.store.create_kill_switch(ks)
    await c.kill_registry.set_kill(ks)
    return {"data": {"kill_id": ks.kill_id, "active": True}}


@router.delete("/kill-switches/{kill_id}")
async def delete_kill(request: Request, kill_id: str):
    await principal_of(request)  # authN required (unkill)
    c = request.app.state.container
    ks = await c.store.get_kill_switch(kill_id)
    if ks is None:
        raise NotFound("kill switch not found")
    await c.store.deactivate_kill_switch(kill_id)
    await c.kill_registry.clear_kill(ks)
    return {"data": {"kill_id": kill_id, "active": False}}
