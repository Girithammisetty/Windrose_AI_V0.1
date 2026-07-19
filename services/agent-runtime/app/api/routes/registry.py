"""agent-registry APIs (ART-FR-001..005, 060..063, 073) + kill switches."""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

from app.api.auth import is_operator, principal_of
from app.domain import policy as policy_mod
from app.domain.entities import (
    AgentDefinition,
    AgentVersion,
    KillSwitch,
    RetrainWatch,
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
                "auto_execute_policy": {}, "self_approval": False, "guardrail_policy": {}}
    return {"agent_key": agent_key, "configured": True, "enabled": cfg.enabled,
            "pinned_version": cfg.pinned_version, "prompt_params": cfg.prompt_params,
            "auto_execute_policy": cfg.auto_execute_policy,
            "self_approval": cfg.self_approval, "guardrail_policy": cfg.guardrail_policy}


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
    # PARTIAL upsert: a field the body omits is PRESERVED, not reset to a default.
    # Previously this rebuilt the whole config and silently wiped guardrail_policy
    # (and prompt_params) whenever a caller PATCHed one facet — so a pack could
    # not attach a security envelope to an agent the tenant also specializes, and
    # a plain enable/disable dropped the envelope. Load the current row and
    # overlay only the provided keys.
    base = await c.store.get_tenant_config(principal.tenant_id, agent_key) or TenantAgentConfig(
        tenant_id=principal.tenant_id, agent_key=agent_key)
    policy = body.get("auto_execute_policy", base.auto_execute_policy)
    policy_mod.validate_auto_policy(policy)  # 422 on destructive/admin auto (AC-5)
    # guardrail_policy: validate + clamp to the operator ceiling when supplied
    # (BRD 53 inc2, PA-FR-060); an explicit {} clears it; omitting it preserves.
    if "guardrail_policy" in body:
        gp_body = body.get("guardrail_policy") or {}
        if not isinstance(gp_body, dict):
            raise EvalGateFailed("guardrail_policy must be an object {data_scope?, budget?, pii?}")
        ceilings = await c.store.get_platform_ceilings()
        guardrail = _validate_guardrail_policy(
            gp_body, budget_ceiling=int(ceilings.get("max_budget_tokens") or _BUDGET_TOKENS_CEILING))  # noqa: E501
    else:
        guardrail = base.guardrail_policy
    cfg = TenantAgentConfig(
        tenant_id=principal.tenant_id, agent_key=agent_key,
        enabled=body.get("enabled", base.enabled),
        pinned_version=body.get("pinned_version", base.pinned_version),
        prompt_params=body.get("prompt_params", base.prompt_params),
        auto_execute_policy=policy, self_approval=body.get("self_approval", base.self_approval),
        guardrail_policy=guardrail)
    await c.store.put_tenant_config(cfg)
    return {"data": _tenant_config_view(agent_key, cfg)}


# ---- BRD 53: tenant-authored CUSTOM agents (config over the shared graph) ----

import re as _re  # noqa: E402

# The ONLY graph a tenant custom agent may run on — the shared, platform-owned,
# eval-gated safe template. A tenant can never name any other graph_ref.
_CUSTOM_GRAPH_REF = "persona_copilot.v1"
# Custom agents cap at write-proposal; higher tiers are operator-fixed-agent only.
_TIER_RANK = {"read": 0, "write-proposal": 1, "write-direct": 2, "admin": 3}

# Platform ceilings for the per-agent guardrail budget (BR-8: a tenant setting can
# never exceed these). The floor is the minimum a single reasoned decision needs;
# a budget below it would make every run refuse, so we reject it at author time.
_BUDGET_TOKENS_CEILING = 200_000
_BUDGET_TOKENS_FLOOR = 128
_UUID_RE = _re.compile(r"^[0-9a-fA-F-]{36}$")


def _validate_guardrail_policy(body: dict, *, budget_ceiling: int = _BUDGET_TOKENS_CEILING) -> dict:
    """Validate + normalize the author-supplied security envelope (PA-FR-060).

    Rejects structurally with the offending field. Cross-tenant data-scope is
    impossible via RLS regardless, so author-time we validate shape (UUID
    workspaces, string dataset urns) and clamp the budget to the operator-set
    platform ceiling (BR-8). Returns the stored guardrail_policy dict ({} when
    nothing was supplied).
    """
    policy: dict = {}

    ds = body.get("data_scope")
    if ds is not None:
        if not isinstance(ds, dict):
            raise EvalGateFailed("data_scope must be an object {workspaces?, dataset_urns?}")
        scope: dict = {}
        ws = ds.get("workspaces")
        if ws is not None:
            if not isinstance(ws, list) or not all(isinstance(w, str) and _UUID_RE.match(w) for w in ws):  # noqa: E501
                raise EvalGateFailed("data_scope.workspaces must be a list of workspace UUIDs")
            scope["workspaces"] = [str(w) for w in ws]
        durns = ds.get("dataset_urns")
        if durns is not None:
            if not isinstance(durns, list) or not all(isinstance(u, str) and u for u in durns):
                raise EvalGateFailed("data_scope.dataset_urns must be a list of dataset URNs")
            scope["dataset_urns"] = [str(u) for u in durns]
        if scope:
            policy["data_scope"] = scope

    budget = body.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            raise EvalGateFailed("budget must be an object {max_tokens_per_session}")
        mt = budget.get("max_tokens_per_session")
        if mt is not None:
            if not isinstance(mt, int) or isinstance(mt, bool) or mt < _BUDGET_TOKENS_FLOOR:
                raise EvalGateFailed(
                    f"budget.max_tokens_per_session must be an integer >= {_BUDGET_TOKENS_FLOOR}")
            # BR-8: clamp DOWN to the operator-set platform ceiling, never up.
            policy["budget"] = {"max_tokens_per_session": min(mt, budget_ceiling)}

    pii = body.get("pii")
    if pii is not None:
        if not isinstance(pii, dict):
            raise EvalGateFailed("pii must be an object {block_pii_egress?, redact?}")
        policy["pii"] = {
            "block_pii_egress": bool(pii.get("block_pii_egress", False)),
            "redact": bool(pii.get("redact", False)),
        }

    return policy


async def _provision_custom_agent(
    c, tenant: str, *, agent_key: str, display_name: str, description: str,
    persona: str, system_prompt: str, allowed_tools: list[str],
    propose_tool: str | None, guardrail_policy: dict,
) -> None:
    """Materialize a tenant custom agent on the shared safe graph: definition +
    v1 (allow-list -> enforced toolset) + tenant config (persona/prompt/propose +
    guardrail envelope). Shared by the interactive author route and persona
    auto-binding (PA-FR-010). The caller owns key derivation + idempotency."""
    await c.store.upsert_agent_definition(AgentDefinition(
        agent_key=agent_key, display_name=display_name, description=description,
        owner_team=f"tenant:{tenant}", default_write_mode="proposal",
        status="published", owner_tenant=tenant))

    toolset = [{"tool_id": t, "version_range": ">=1.0.0"} for t in allowed_tools]
    card = build_card(agent_key=agent_key, version=1, display_name=display_name,
                      description=description, write_mode="proposal",
                      skills=[], endpoint=f"https://agent-runtime.internal/a2a/{agent_key}",
                      eval_score_ref="persona-copilot-shared-gate")
    sig = sign_card(c.signing_key, card)
    card["signature"] = {"alg": "RS256", "kid": c.signing_key.kid, "value": sig}
    await c.store.create_agent_version(AgentVersion(
        agent_key=agent_key, version=1, graph_ref=_CUSTOM_GRAPH_REF,
        graph_digest=graph_digest(_CUSTOM_GRAPH_REF), toolset=toolset,
        model_config={"request_class": "chat", "max_rung": 1, "temperature": 0.2},
        memory_policy={"scopes_readable": ["workspace", "tenant"], "scopes_writable": []},
        eval_gate={"suite_id": "persona-copilot-suite"},
        eval_gate_result_id="persona-copilot-shared-gate",
        a2a_card=card, card_signature=sig,
        principal_ref=f"spiffe://windrose/ns/ai/agent/{agent_key}", status="published"))

    await c.store.put_tenant_config(TenantAgentConfig(
        tenant_id=tenant, agent_key=agent_key, enabled=True,
        prompt_params={"persona": persona, "system_prompt": system_prompt[:2000],
                       "propose_tool": propose_tool},
        auto_execute_policy={}, self_approval=False,
        guardrail_policy=guardrail_policy))


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
    # The tier ceiling is operator-set (BRD 53 inc3), but never above the hard
    # write-proposal cap for custom agents (destructive/admin stay fixed-agent).
    ceilings = await c.store.get_platform_ceilings()
    tier_ceiling = str(ceilings.get("max_tier") or "write-proposal")
    if _TIER_RANK.get(tier_ceiling, 99) > _TIER_RANK["write-proposal"]:
        tier_ceiling = "write-proposal"
    max_tier = str(body.get("max_tier") or "write-proposal")
    if _TIER_RANK.get(max_tier, 99) > _TIER_RANK.get(tier_ceiling, 1):
        raise EvalGateFailed(
            f"max_tier {max_tier!r} exceeds the platform ceiling ({tier_ceiling})")
    # A tenant may NEVER name another graph_ref — it is forced to the shared one.
    if body.get("graph_ref") and body["graph_ref"] != _CUSTOM_GRAPH_REF:
        raise EvalGateFailed(
            f"custom agents run only on {_CUSTOM_GRAPH_REF} (graph_ref is not configurable)")

    # BRD 53 inc2: the per-agent security envelope (data-scope / budget / pii),
    # validated + clamped to the operator ceiling (PA-FR-060) and enforced in the
    # shared graph.
    guardrail_policy = _validate_guardrail_policy(
        body, budget_ceiling=int(ceilings.get("max_budget_tokens") or _BUDGET_TOKENS_CEILING))

    slug = _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "agent"
    agent_key = f"cust-{str(tenant).replace('-', '')[:8]}-{slug}"
    if await c.store.get_agent_definition(agent_key) is not None:
        raise Conflict(f"a custom agent named {name!r} already exists")

    await _provision_custom_agent(
        c, tenant, agent_key=agent_key, display_name=name,
        description=body.get("description", f"Tenant custom copilot for {persona}"),
        persona=persona, system_prompt=str(body.get("system_prompt") or ""),
        allowed_tools=allowed_tools, propose_tool=propose_tool,
        guardrail_policy=guardrail_policy)

    return {"data": {"agent_key": agent_key, "status": "published",
                     "graph_ref": _CUSTOM_GRAPH_REF, "allowed_tools": allowed_tools,
                     "persona": persona, "owner_tenant": tenant,
                     "guardrail_policy": guardrail_policy}}


@router.post("/tenants/self/personas/autobind")
async def autobind_persona_copilots(request: Request, body: dict = Body(...)):
    """PA-FR-010: bind persona copilots for a tenant's (pack-shipped) roles. For
    each role name that does not already have a persona copilot, provision one on
    the shared safe graph, grounded in that persona. Idempotent by a deterministic
    key (persona-<tenant8>-<roleslug>) so re-running after a pack update only fills
    the gaps. Advisory by default (no propose tool) unless propose_tool is given
    (which must be a governed tool the shared graph supports). Needs ai.agent.admin."""
    principal = await principal_of(request)
    await _require_agent_cap(request, principal, "ai.agent.admin")
    c = request.app.state.container
    tenant = principal.tenant_id

    roles = body.get("roles") or []
    if not isinstance(roles, list) or not all(isinstance(r, str) and r.strip() for r in roles):
        raise EvalGateFailed("roles must be a non-empty list of role names")
    roles = [r.strip() for r in roles if r.strip()]
    if not roles:
        raise EvalGateFailed("roles must be a non-empty list of role names")

    propose_tool = body.get("propose_tool")  # None = advisory persona copilot
    # The shared graph knows how to safely propose only its supported tools; a
    # persona copilot's allow-list is that one tool (advisory ones keep it on the
    # list but never propose — propose_tool stays null).
    default_tool = "case.apply_disposition"
    if propose_tool is not None and not isinstance(propose_tool, str):
        raise EvalGateFailed("propose_tool must be a tool id string or omitted")

    created: list[dict] = []
    skipped: list[dict] = []
    t8 = str(tenant).replace("-", "")[:8]
    for role in roles:
        rslug = _re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")[:40] or "role"
        agent_key = f"persona-{t8}-{rslug}"
        if await c.store.get_agent_definition(agent_key) is not None:
            skipped.append({"role": role, "agent_key": agent_key})
            continue
        await _provision_custom_agent(
            c, tenant, agent_key=agent_key, display_name=f"{role} Copilot",
            description=f"Role-grounded copilot for the {role} persona (auto-bound).",
            persona=role,
            system_prompt=(f"You are the copilot for the {role}. Ground every "
                           "recommendation in what that role is permitted to do, and "
                           "only ever recommend governed outcomes a human approves."),
            allowed_tools=[propose_tool or default_tool],
            propose_tool=propose_tool, guardrail_policy={})
        created.append({"role": role, "agent_key": agent_key})

    return {"data": {"created": created, "skipped": skipped}}


@router.get("/platform/agent-ceilings")
async def get_agent_ceilings(request: Request):
    """Read the platform ceilings that clamp every tenant custom agent (BRD 53
    inc3). Operator-only — these are the maximums no tenant setting can exceed."""
    principal = await principal_of(request)
    _require_operator(principal)
    c = request.app.state.container
    ce = await c.store.get_platform_ceilings()
    return {"data": {"max_budget_tokens": int(ce.get("max_budget_tokens") or 200000),
                     "max_tier": ce.get("max_tier") or "write-proposal",
                     "updated_at": (ce.get("updated_at").isoformat()
                                    if ce.get("updated_at") else None),
                     "updated_by": ce.get("updated_by")}}


@router.put("/platform/agent-ceilings")
async def put_agent_ceilings(request: Request, body: dict = Body(...)):
    """Set the platform ceilings (operator-only). Budget must be a positive int
    within the hard maximum; tier can never be set above write-proposal for
    custom agents. Tightening applies to every subsequent author/clamp."""
    principal = await principal_of(request)
    _require_operator(principal)
    c = request.app.state.container

    mb = body.get("max_budget_tokens")
    if not isinstance(mb, int) or isinstance(mb, bool) or mb < _BUDGET_TOKENS_FLOOR or mb > _BUDGET_TOKENS_CEILING:  # noqa: E501
        raise EvalGateFailed(
            f"max_budget_tokens must be an integer in [{_BUDGET_TOKENS_FLOOR}, {_BUDGET_TOKENS_CEILING}]")  # noqa: E501
    mt = str(body.get("max_tier") or "write-proposal")
    if _TIER_RANK.get(mt, 99) > _TIER_RANK["write-proposal"]:
        raise EvalGateFailed(f"max_tier {mt!r} cannot exceed write-proposal for custom agents")

    await c.store.set_platform_ceilings(
        max_budget_tokens=mb, max_tier=mt,
        updated_by=(principal.sub if principal else None))
    return {"data": {"max_budget_tokens": mb, "max_tier": mt}}


def _watch_view(w: RetrainWatch) -> dict:
    return {"id": w.id, "model_urn": w.model_urn, "watched_agent_key": w.watched_agent_key,
            "workspace_id": w.workspace_id, "cadence_seconds": w.cadence_seconds,
            "correction_window_hours": w.correction_window_hours,
            "drift_threshold": w.drift_threshold, "min_corrections": w.min_corrections,
            "enabled": w.enabled,
            "last_checked_at": w.last_checked_at.isoformat() if w.last_checked_at else None,
            "last_signal": w.last_signal, "created_by": w.created_by}


@router.get("/retrain-watches")
async def list_retrain_watches(request: Request):
    """The caller-tenant's scheduled retrain watches (BRD 52 inc3). ai.agent.admin."""
    principal = await principal_of(request)
    await _require_agent_cap(request, principal, "ai.agent.admin")
    c = request.app.state.container
    rows = await c.store.list_retrain_watches(principal.tenant_id)
    return {"data": [_watch_view(w) for w in rows]}


@router.post("/retrain-watches")
async def create_retrain_watch(request: Request, body: dict = Body(...)):
    """Register a scheduled drift watch on a deployed model. The scheduler counts
    human corrections to watched_agent_key's proposals on the cadence and, over
    the threshold, opens a four-eyes retrain proposal via the governance agent.
    Needs ai.agent.admin."""
    principal = await principal_of(request)
    await _require_agent_cap(request, principal, "ai.agent.admin")
    c = request.app.state.container

    model_urn = str(body.get("model_urn") or "").strip()
    if not model_urn:
        raise EvalGateFailed("model_urn is required")
    watched = str(body.get("watched_agent_key") or "").strip()
    if not watched:
        raise EvalGateFailed("watched_agent_key is required (whose proposals reflect this model)")

    def _pos_int(v, default, lo=1):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return default
        return n if n >= lo else default

    threshold = body.get("drift_threshold")
    try:
        threshold = float(threshold) if threshold is not None else 0.3
    except (TypeError, ValueError):
        threshold = 0.3
    if not 0.0 <= threshold <= 1.0:
        raise EvalGateFailed("drift_threshold must be between 0 and 1")

    w = RetrainWatch(
        id=new_uuid(), tenant_id=principal.tenant_id, model_urn=model_urn,
        watched_agent_key=watched, workspace_id=body.get("workspace_id"),
        cadence_seconds=_pos_int(body.get("cadence_seconds"), 86400, 60),
        correction_window_hours=_pos_int(body.get("correction_window_hours"), 168),
        drift_threshold=threshold,
        min_corrections=_pos_int(body.get("min_corrections"), 20, 0),
        enabled=bool(body.get("enabled", True)),
        created_by=(principal.sub if principal else None))
    await c.store.create_retrain_watch(w)
    return {"data": _watch_view(w)}


@router.delete("/retrain-watches/{watch_id}")
async def delete_retrain_watch(request: Request, watch_id: str):
    principal = await principal_of(request)
    await _require_agent_cap(request, principal, "ai.agent.admin")
    c = request.app.state.container
    ok = await c.store.delete_retrain_watch(principal.tenant_id, watch_id)
    if not ok:
        raise NotFound("retrain watch not found")
    return {"data": {"id": watch_id, "deleted": True}}


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
    principal = await principal_of(request)
    c = request.app.state.container
    ks = await c.store.get_kill_switch(kill_id)
    if ks is None:
        raise NotFound("kill switch not found")
    # Lifting a kill RE-ENABLES a disabled/killed agent (a safety control), so it
    # must be at least as privileged as SETTING one — same authz as create_kill:
    # operator, or ai.agent.admin for a tenant-scoped kill on the caller's OWN
    # tenant. Scope the lookup to the caller's tenant (get_kill_switch is a
    # BYPASSRLS admin read) so a tenant admin cannot lift another tenant's kill.
    tenant_scoped = ks.scope == "agent_version_tenant"
    if not (is_operator(principal)
            or (tenant_scoped and ks.tenant_id == principal.tenant_id
                and await _has_agent_cap(request, principal, "ai.agent.admin"))):
        raise PermissionDenied("operator (or ai.agent.admin for own-tenant scope) required")
    await c.store.deactivate_kill_switch(kill_id)
    await c.kill_registry.clear_kill(ks)
    return {"data": {"kill_id": kill_id, "active": False}}
