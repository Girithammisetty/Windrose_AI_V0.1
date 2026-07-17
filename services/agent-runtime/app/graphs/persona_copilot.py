"""persona-copilot (BRD 53) — the SHARED, SAFE, config-driven graph that every
tenant custom agent runs on. Tenants supply intent + constraints as
configuration (persona, system prompt, allow-listed propose tool); they never
supply code, and the graph topology is fixed and platform-owned.

It grounds on a case + the tenant's real disposition catalog (read-only, RLS-
scoped), reasons with the REAL model (ai-gateway — already prompt-injection- and
PII-guarded on that path) using the TENANT-authored system prompt bound to the
persona, and PROPOSES the single configured tool as a WriteIntent — which the
runtime converts to a four-eyes Proposal. The proposal is additionally checked
against the agent's declared toolset allow-list at ProposalService.create_from_
intent (PA-FR-030), so a misconfigured or manipulated graph still cannot act
outside the envelope.

Config (read from ``deps.prompt_params``):
  persona:       rbac role/label the copilot is grounded in (relevance + prompt)
  system_prompt: the tenant-authored instruction (bounded, sanitised at author time)
  propose_tool:  the ONE tool this copilot may propose (MUST be on the agent's
                 allow-list); omit → read-only advisory copilot (no write intent)

Only ``case.apply_disposition`` is a supported propose_tool in increment 1 (the
disposition copilot); an unknown propose_tool yields a read-only answer rather
than an unsafe write. Real LangGraph StateGraph: ground -> reason -> propose.
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, StateGraph

from app.adapters.memory import GroundingDegraded
from app.domain.urn import case_urn
from app.graphs.base import GraphDeps, GraphOutcome, WriteIntent, register
from app.graphs.persona import caller_persona
from app.graphs.triage import (
    SEVERITIES,
    TRIAGE_TOOL_ID,
    TRIAGE_TOOL_VERSION,
    _extract_json,
    _normalise,
    _resolve_disposition_id,
)

# Tools this shared graph knows how to safely propose. A custom agent may only
# name one of these as its propose_tool; anything else degrades to read-only.
_SUPPORTED_PROPOSE_TOOLS = {TRIAGE_TOOL_ID}

_BASE_SYS = (
    "You are a Windrose decision copilot operating for a specific tenant persona. "
    "Follow the tenant's instruction below, but you may ONLY recommend outcomes "
    "the platform governs — you never take an action directly; a human approves "
    "every recommendation. Respond with ONLY a JSON object: "
    '{"severity": one of ["low","medium","high","critical"], '
    '"disposition_code": the "code" of ONE entry from the given disposition '
    'catalog (copy it exactly; inventing a code is not allowed), '
    '"rationale": one concise sentence citing the evidence}. No prose outside JSON.'
)


def build_persona_copilot_graph(deps: GraphDeps):
    cfg = deps.prompt_params or {}
    propose_tool = cfg.get("propose_tool")

    async def ground(state: dict) -> dict:
        case: dict[str, Any] = {}
        dispositions: list[dict] = []
        if deps.case_reader is not None and state.get("case_id"):
            case = await deps.case_reader.get_case(
                tenant_id=state["tenant_id"], case_id=state["case_id"],
                auth_token=deps.obo_token or "")
            if hasattr(deps.case_reader, "list_dispositions"):
                dispositions = await deps.case_reader.list_dispositions(
                    tenant_id=state["tenant_id"], auth_token=deps.obo_token or "")
        state["case"] = case
        state["dispositions"] = dispositions
        memories: list[dict] = []
        if deps.memory is not None and case:
            try:
                memories = await deps.memory.retrieve(
                    tenant_id=state["tenant_id"],
                    query=json.dumps(case, default=str)[:400],
                    auth_token=deps.obo_token or "", top_k=5,
                    snapshot_ver=deps.memory_snapshot_ver)
            except GroundingDegraded as exc:
                state.setdefault("trace", []).append(
                    {"event": "grounding_degraded", "source": "memory-service",
                     "status": exc.status_code})
        state["memories"] = memories
        state.setdefault("trace", []).append(
            {"event": "tool_call_result", "tool_id": "case.get",
             "digest": state.get("case_id"), "dispositions": len(dispositions)})
        return state

    async def reason(state: dict) -> dict:
        persona = caller_persona(state.get("caller"), cfg)
        tenant_instruction = str(cfg.get("system_prompt") or "").strip()[:2000]
        catalog = [{"code": d.get("code"), "label": d.get("label")}
                   for d in state.get("dispositions", [])][:40]
        mems = [m.get("content", m) for m in state.get("memories", [])]
        user = (
            f"Persona: {persona}\n"
            f"Tenant instruction: {tenant_instruction or '(none)'}\n"
            f"Claim case (JSON): {json.dumps(state.get('case') or {}, default=str)[:1500]}\n"
            f"Similar resolved cases: {json.dumps(mems, default=str)[:1000]}\n"
            f"Disposition catalog (pick disposition_code from here): "
            f"{json.dumps(catalog, default=str)}\n"
            "Decide the disposition now."
        )
        result = await deps.llm.chat(
            messages=[{"role": "system", "content": _BASE_SYS},
                      {"role": "user", "content": user}],
            tenant_id=state["tenant_id"],
            response_format={"type": "json_object"},
            temperature=0.2, max_tokens=300)
        state["usage"] = {"input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens,
                          "model": result.model, "deployment": result.deployment}
        state["disposition"] = _normalise(_extract_json(result.content), state)
        state.setdefault("trace", []).append(
            {"event": "reflection", "iteration": 0, "model": result.model})
        return state

    async def propose(state: dict) -> dict:
        # Read-only advisory copilot: no configured/supported propose tool → no
        # write intent, just the reasoned answer. Fail SAFE, never guess a tool.
        if propose_tool not in _SUPPORTED_PROPOSE_TOOLS or not state.get("case_id"):
            return state
        d = state["disposition"]
        disposition_id = _resolve_disposition_id(
            d["disposition_code"], state.get("dispositions", []), state)
        args = {"case_id": state["case_id"], "severity": d["severity"],
                "disposition_id": disposition_id, "resolution_note": d["rationale"]}
        workspace_id = (state.get("case") or {}).get("workspace_id")
        state["write_intent"] = WriteIntent(
            tool_id=TRIAGE_TOOL_ID, tool_version=TRIAGE_TOOL_VERSION,
            tier="write-proposal", side_effects="reversible", args=args,
            rationale=d["rationale"],
            affected_urns=[case_urn(state["tenant_id"], state["case_id"])],
            workspace_id=workspace_id, required_action="case.case.update",
            predicted_effect={
                "summary": (f"Case {state['case_id']} severity -> {d['severity']}, "
                            f"disposition {d['disposition_code']} (custom copilot)."),
                "reversibility": "reversible", "blast_radius": 1})
        state.setdefault("trace", []).append(
            {"event": "proposal_created", "tool_id": TRIAGE_TOOL_ID})
        return state

    g = StateGraph(dict)
    g.add_node("ground", ground)
    g.add_node("reason", reason)
    g.add_node("propose", propose)
    g.set_entry_point("ground")
    g.add_edge("ground", "reason")
    g.add_edge("reason", "propose")
    g.add_edge("propose", END)
    return g.compile()


@register("persona_copilot.v1")
def persona_copilot_module():
    return build_persona_copilot_graph


async def run_persona_copilot(deps: GraphDeps, inputs: dict) -> GraphOutcome:
    graph = build_persona_copilot_graph(deps)
    final = await graph.ainvoke(dict(inputs))
    d = final.get("disposition") or {}
    advisory = final.get("write_intent") is None
    text = (f"Advisory: recommend disposition {d.get('disposition_code')} "
            f"(severity {d.get('severity')}). {d.get('rationale', '')}" if advisory
            else f"Proposed disposition {d.get('disposition_code')} "
                 f"(severity {d.get('severity')}) for approval.")
    return GraphOutcome(
        final_text=text.strip(),
        write_intent=final.get("write_intent"),
        usage=final.get("usage", {}),
        trace=final.get("trace", []),
        structured={"persona": (deps.prompt_params or {}).get("persona"),
                    "advisory": advisory, **d},
        evidence=final.get("memories", []))


_ = SEVERITIES  # re-exported intent: severities validated in _normalise
