"""case-triage copilot (PRIORITY, ART-FR-040/041).

Reads a claim case from case-service + relevant resolved-case memory (RAG via
memory-service), reasons with the REAL model (ai-gateway -> Ollama), and PROPOSES
a disposition (severity / assignee / disposition-code) as a WRITE INTENT — never a
direct write. The runtime converts the intent into a Proposal requiring human
approval; on approve it executes via a tool-plane write-proposal tool under a
signed grant.

Real LangGraph StateGraph: ground -> reason -> propose.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langgraph.graph import END, StateGraph

from app.adapters.memory import GroundingDegraded
from app.domain.urn import case_urn
from app.graphs.base import GraphDeps, GraphOutcome, WriteIntent, register
from app.graphs.persona import caller_persona

SEVERITIES = ("low", "medium", "high", "critical")

TRIAGE_TOOL_ID = "case.apply_disposition"
# Must match tool-plane's currently-published version (tool_versions.status='published')
# exactly — lookup is exact-match, not range-based, so a stale pin here silently
# denies every disposition-apply call with NOT_FOUND (confirmed live 2026-07-17:
# tool-plane only had 1.2.0 published while this was still pinned to 1.0.0).
TRIAGE_TOOL_VERSION = "1.2.0"

_SYS = (
    "You are Windrose's insurance claims triage copilot. Given a claim case, "
    "similar resolved cases, and the tenant's real disposition catalog, decide a "
    "disposition. Respond with ONLY a JSON object: "
    '{"severity": one of ["low","medium","high","critical"], '
    '"disposition_code": the "code" of ONE entry from the given disposition '
    "catalog (copy it exactly — inventing a code is not allowed), "
    '"rationale": one concise sentence citing the evidence}. No prose outside JSON.'
)


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def build_triage_graph(deps: GraphDeps):
    async def ground(state: dict) -> dict:
        case: dict[str, Any] = {}
        memories: list[dict] = []
        dispositions: list[dict] = []
        if deps.case_reader is not None:
            case = await deps.case_reader.get_case(
                tenant_id=state["tenant_id"], case_id=state["case_id"],
                auth_token=deps.obo_token or "")
            if hasattr(deps.case_reader, "list_dispositions"):
                dispositions = await deps.case_reader.list_dispositions(
                    tenant_id=state["tenant_id"], auth_token=deps.obo_token or "")
        state["dispositions"] = dispositions
        query = _case_query(case, state)
        if deps.memory is not None:
            try:
                # In replay mode (ART-FR-015) RAG reads are pinned to the requested
                # corpus snapshot for deterministic grounding; live runs pass None.
                memories = await deps.memory.retrieve(
                    tenant_id=state["tenant_id"], query=query,
                    auth_token=deps.obo_token or "", top_k=5,
                    snapshot_ver=deps.memory_snapshot_ver)
            except GroundingDegraded as exc:
                # 401/403 from memory-service: proceed ungrounded but make the
                # degradation VISIBLE in the run trace/state (never silent).
                memories = []
                state["grounding_degraded"] = {"status": exc.status_code,
                                               "source": "memory-service"}
                state.setdefault("trace", []).append(
                    {"event": "grounding_degraded", "source": "memory-service",
                     "status": exc.status_code})
        state["case"] = case
        state["memories"] = memories
        state.setdefault("trace", []).append(
            {"event": "tool_call_result", "tool_id": "case.get",
             "digest": state["case_id"], "memories": len(memories)})
        return state

    async def reason(state: dict) -> dict:
        # Persona is the invoking user's role when resolved (role-grounding,
        # ART-FR-040), else the tenant-configured persona.
        persona = caller_persona(state.get("caller"), deps.prompt_params)
        case_json = json.dumps(state.get("case") or {}, default=str)[:1500]
        mems = [m.get("content", m) for m in state.get("memories", [])]
        mem_json = json.dumps(mems, default=str)[:1200]
        catalog = [{"code": d.get("code"), "label": d.get("label")}
                   for d in state.get("dispositions", [])][:40]
        catalog_json = json.dumps(catalog, default=str)
        user = (
            f"Persona: {persona}\n"
            f"Claim case (JSON): {case_json}\n"
            f"Similar resolved cases: {mem_json}\n"
            f"Disposition catalog (pick disposition_code from here): {catalog_json}\n"
            "Decide the disposition now."
        )
        result = await deps.llm.chat(
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": user}],
            tenant_id=state["tenant_id"],
            response_format={"type": "json_object"},
            temperature=0.2, max_tokens=300)
        parsed = _extract_json(result.content)
        state["model_output"] = result.content
        state["usage"] = {"input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens,
                          "model": result.model, "deployment": result.deployment}
        state["disposition"] = _normalise(parsed, state)
        state.setdefault("trace", []).append(
            {"event": "reflection", "iteration": 0, "model": result.model})
        return state

    async def propose(state: dict) -> dict:
        d = state["disposition"]
        # case.apply_disposition's real (published) input schema is
        # additionalProperties:false and requires disposition_id (a real
        # dispositions-table row, not free text) — it has no assignee_id/
        # workspace_id fields at all. Resolve the model's chosen
        # disposition_code against the real catalog fetched in ground().
        disposition_id = _resolve_disposition_id(
            d["disposition_code"], state.get("dispositions", []), state)
        args = {"case_id": state["case_id"], "severity": d["severity"],
                "disposition_id": disposition_id,
                "resolution_note": d["rationale"]}
        # workspace_id is NOT a tool arg (case.apply_disposition's schema is
        # additionalProperties:false and has no such field) — it travels on
        # WriteIntent.workspace_id instead, purely for the caller-gate's
        # workspace-scoped case.case.update check (ART-FR-044).
        workspace_id = (state.get("case") or {}).get("workspace_id")
        state["write_intent"] = WriteIntent(
            tool_id=TRIAGE_TOOL_ID, tool_version=TRIAGE_TOOL_VERSION,
            tier="write-proposal", side_effects="reversible", args=args,
            rationale=d["rationale"],
            affected_urns=[case_urn(state["tenant_id"], state["case_id"])],
            workspace_id=workspace_id,
            # Applying a disposition mutates the case (severity/assignee): the
            # invoking caller must hold case.case.update to propose it.
            required_action="case.case.update",
            predicted_effect={
                "summary": (f"Case {state['case_id']} severity -> {d['severity']}, "
                            f"disposition {d['disposition_code']}; SLA timer restarts."),
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


def _case_query(case: dict, state: dict) -> str:
    proj = case.get("display_projection") or {}
    bits = [f"{k}={v}" for k, v in list(proj.items())[:6]]
    return f"triage claim {state['case_id']} " + " ".join(bits)


def _normalise(parsed: dict, state: dict) -> dict:
    sev = str(parsed.get("severity", "")).lower()
    if sev not in SEVERITIES:
        sev = "medium"
    raw_code = str(parsed.get("disposition_code", "needs_review")).lower()
    code = re.sub(r"[^a-z0-9_]+", "_", raw_code)
    rationale = str(parsed.get("rationale")
                    or "Model-assessed disposition based on case + precedent.")
    return {"severity": sev, "disposition_code": code[:64] or "needs_review",
            "rationale": rationale[:4000]}


def _resolve_disposition_id(code: str, dispositions: list[dict], state: dict) -> str:
    """Match the model's chosen disposition_code against the real catalog.

    disposition_id is a required, real-row UUID in case.apply_disposition's
    published schema — the model can only ever choose a code (a human label),
    so this is the one place that translates code -> id. Falls back to the
    catalog's first entry (recorded in the trace, never silent) when the
    model's code doesn't match, since a proposal with no disposition_id would
    always fail tool-plane schema validation.
    """
    for d in dispositions:
        if str(d.get("code", "")).lower() == code.lower():
            return d["id"]
    if dispositions:
        state.setdefault("trace", []).append(
            {"event": "disposition_code_fallback", "requested_code": code,
             "fallback_id": dispositions[0]["id"]})
        return dispositions[0]["id"]
    raise ValueError(
        f"no dispositions available in tenant catalog to resolve code={code!r}")


@register("triage.v1")
def triage_module():
    return build_triage_graph


async def run_triage(deps: GraphDeps, inputs: dict) -> GraphOutcome:
    graph = build_triage_graph(deps)
    state = dict(inputs)
    final = await graph.ainvoke(state)
    return GraphOutcome(
        final_text=(f"Proposed disposition for case {inputs['case_id']}: "
                    f"{final['disposition']['severity']} / "
                    f"{final['disposition']['disposition_code']}."),
        write_intent=final.get("write_intent"),
        usage=final.get("usage", {}),
        trace=final.get("trace", []),
        structured=final.get("disposition", {}),
        evidence=final.get("memories", []))
