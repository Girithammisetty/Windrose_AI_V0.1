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
from app.domain.redact import redact_text
from app.domain.urn import case_urn
from app.graphs.base import GraphDeps, GraphOutcome, WriteIntent, register
from app.graphs.persona import caller_persona
from app.graphs.triage import (
    SEVERITIES,
    TRIAGE_TOOL_ID,
    TRIAGE_TOOL_VERSION,
    _extract_json,
    _fetch_evidence,
    _format_evidence,
    _normalise,
    _resolve_disposition_id,
)
from app.prompts import system_prompt

# Tools this shared graph knows how to safely propose. A custom agent may only
# name one of these as its propose_tool; anything else degrades to read-only.
_SUPPORTED_PROPOSE_TOOLS = {TRIAGE_TOOL_ID}

_BASE_SYS = system_prompt("persona_copilot.system")


def build_persona_copilot_graph(deps: GraphDeps):
    cfg = deps.prompt_params or {}
    propose_tool = cfg.get("propose_tool")
    # BRD 53 inc2 (PA-FR-001): the machine-enforced security envelope, applied
    # here independent of the prompt.
    policy = deps.guardrail_policy or {}
    data_scope = policy.get("data_scope") or {}
    allowed_workspaces = {str(w) for w in (data_scope.get("workspaces") or [])}
    budget = policy.get("budget") or {}

    async def ground(state: dict) -> dict:
        case: dict[str, Any] = {}
        dispositions: list[dict] = []
        if deps.case_reader is not None and state.get("case_id"):
            case = await deps.case_reader.get_case(
                tenant_id=state["tenant_id"], case_id=state["case_id"],
                auth_token=deps.obo_token or "")
            # PA-FR-040 data-scope enforcement: an agent scoped to specific
            # workspaces may NOT read a case outside them, even when the invoking
            # human could — data_scope is additive to RLS, never a relaxation
            # (BR-7). An out-of-scope read returns empty + a logged refusal; the
            # graph then produces an out-of-scope advisory with no write intent.
            if allowed_workspaces and str((case or {}).get("workspace_id") or "") not in allowed_workspaces:  # noqa: E501
                state["out_of_scope"] = True
                state["case"] = {}
                state["dispositions"] = []
                state.setdefault("trace", []).append(
                    {"event": "data_scope_refusal", "case_id": state.get("case_id"),
                     "case_workspace": (case or {}).get("workspace_id"),
                     "allowed_workspaces": sorted(allowed_workspaces)})
                return state
            if hasattr(deps.case_reader, "list_dispositions"):
                dispositions = await deps.case_reader.list_dispositions(
                    tenant_id=state["tenant_id"], auth_token=deps.obo_token or "")
        state["case"] = case
        state["dispositions"] = dispositions
        # Reason over the case's attached documents (not just the row projection).
        await _fetch_evidence(deps, state)
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
        # A refused (out-of-scope) read never reaches the model — no grounding,
        # no LLM spend, no proposal.
        if state.get("out_of_scope"):
            return state
        persona = caller_persona(state.get("caller"), cfg)
        tenant_instruction = str(cfg.get("system_prompt") or "").strip()[:2000]
        catalog = [{"code": d.get("code"), "label": d.get("label")}
                   for d in state.get("dispositions", [])][:40]
        mems = [m.get("content", m) for m in state.get("memories", [])]
        evidence_block = _format_evidence(state.get("evidence_docs", []))
        user = (
            f"Persona: {persona}\n"
            f"Tenant instruction: {tenant_instruction or '(none)'}\n"
            f"Claim case (JSON): {json.dumps(state.get('case') or {}, default=str)[:1500]}\n"
            f"Similar resolved cases: {json.dumps(mems, default=str)[:1000]}\n"
            f"{evidence_block}"
            f"Disposition catalog (pick disposition_code from here): "
            f"{json.dumps(catalog, default=str)}\n"
            "Decide the disposition now."
        )
        # Per-agent budget: cap this run's output tokens at the agent's
        # max_tokens_per_session (never above the default 300). ai-gateway is the
        # cumulative cross-turn budget authority; this is the deterministic
        # per-run ceiling enforced in-graph.
        max_toks = 300
        if budget.get("max_tokens_per_session"):
            max_toks = max(1, min(300, int(budget["max_tokens_per_session"])))
        result = await deps.llm.chat(
            messages=[{"role": "system", "content": _BASE_SYS},
                      {"role": "user", "content": user}],
            tenant_id=state["tenant_id"],
            response_format={"type": "json_object"},
            temperature=0.2, max_tokens=max_toks)
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
        # An out-of-scope read never yields a proposal either.
        if (state.get("out_of_scope") or propose_tool not in _SUPPORTED_PROPOSE_TOOLS
                or not state.get("case_id")):
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

    # PA-FR-040: a read the agent's data-scope forbade produces a clean, logged
    # out-of-scope answer — never a recommendation over data it may not see.
    if final.get("out_of_scope"):
        return GraphOutcome(
            final_text="This case is outside this agent's data scope — no recommendation was made.",
            write_intent=None, usage=final.get("usage", {}), trace=final.get("trace", []),
            structured={"persona": (deps.prompt_params or {}).get("persona"),
                        "advisory": True, "out_of_scope": True},
            evidence=[])

    d = final.get("disposition") or {}
    advisory = final.get("write_intent") is None
    text = (f"Advisory: recommend disposition {d.get('disposition_code')} "
            f"(severity {d.get('severity')}). {d.get('rationale', '')}" if advisory
            else f"Proposed disposition {d.get('disposition_code')} "
                 f"(severity {d.get('severity')}) for approval.")
    text = text.strip()
    write_intent = final.get("write_intent")

    # PII-egress guard (PA-FR-001 pii): when the agent's policy blocks/redacts PII,
    # scrub common direct identifiers from everything the agent emits — the answer
    # text and the proposal's human-facing rationale/summary — before it leaves the
    # graph. Deterministic, independent of the model's cooperation.
    pii = (deps.guardrail_policy or {}).get("pii") or {}
    if pii.get("block_pii_egress") or pii.get("redact"):
        text = redact_text(text)
        if d.get("rationale"):
            d = {**d, "rationale": redact_text(str(d["rationale"]))}
        if write_intent is not None:
            write_intent.rationale = redact_text(write_intent.rationale or "")
            summary = (write_intent.predicted_effect or {}).get("summary")
            if summary:
                write_intent.predicted_effect = {
                    **write_intent.predicted_effect, "summary": redact_text(str(summary))}

    return GraphOutcome(
        final_text=text,
        write_intent=write_intent,
        usage=final.get("usage", {}),
        trace=final.get("trace", []),
        structured={"persona": (deps.prompt_params or {}).get("persona"),
                    "advisory": advisory, **d},
        evidence=final.get("memories", []))


_ = SEVERITIES  # re-exported intent: severities validated in _normalise
