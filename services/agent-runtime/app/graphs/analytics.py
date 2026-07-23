"""analytics agent graph (ART-FR-013) — read-only, framework-complete.

Preserves the chat-agent-service shape: ground -> query_analyzer (LLM) ->
[call_tool] -> reflection loop (max_reflections, skip when no data tool used).
It is read-only: it never emits a WriteIntent. Data tools are called via
tool-plane in the platform target; here the graph is wired for the framework +
reflection control flow and returns a grounded answer. Full semantic-layer
tool wiring is Phase-2 follow-up.

When the caller (e.g. the case-detail Copilot drawer) supplies a case_id, the
``ground`` node fetches that case + its evidence attachments (the same reader
helpers triage/persona_copilot use — best-effort, XPIA-defended, never
raises) so the model actually answers about the specific case instead of
giving generic, context-free guidance. No case_id -> ground is a no-op and
behaviour is unchanged.
"""

from __future__ import annotations

import json

from langgraph.graph import END, StateGraph

from app.graphs.base import GraphDeps, GraphOutcome, register
from app.graphs.persona import role_directive
from app.graphs.triage import _fetch_evidence, _format_evidence
from app.prompts import system_prompt

_SYS = system_prompt("analytics.system")


def build_analytics_graph(deps: GraphDeps):
    async def ground(state: dict) -> dict:
        case: dict = {}
        if deps.case_reader is not None and state.get("case_id"):
            case = await deps.case_reader.get_case(
                tenant_id=state["tenant_id"], case_id=state["case_id"],
                auth_token=deps.obo_token or "")
            await _fetch_evidence(deps, state)
            state.setdefault("trace", []).append(
                {"event": "case_grounded", "case_id": state.get("case_id"),
                 "docs": len(state.get("evidence_docs") or [])})
        state["case"] = case
        return state

    async def query_analyzer(state: dict) -> dict:
        # Role-ground the tone: a technical user gets precise terminology, an
        # operational user gets plain language (ART-FR-040). Empty when unknown.
        sys = _SYS
        directive = role_directive(state.get("caller"))
        if directive:
            sys = f"{_SYS} {directive}"
        user_content = state["query"]
        case = state.get("case") or {}
        if case:
            evidence_block = _format_evidence(state.get("evidence_docs", []))
            user_content = (
                f"Case (JSON): {json.dumps(case, default=str)[:1500]}\n"
                f"{evidence_block}"
                f"Question: {state['query']}\n"
                "Answer using ONLY the case data and evidence above; say what "
                "you don't know rather than guessing."
            )
        result = await deps.llm.chat(
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": user_content}],
            tenant_id=state["tenant_id"], temperature=0.2, max_tokens=300)
        state["draft"] = result.content
        state["usage"] = {"input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens, "model": result.model}
        state["used_data_tool"] = bool(state.get("used_data_tool"))
        state["reflection_count"] = state.get("reflection_count", 0)
        return state

    async def reflection(state: dict) -> dict:
        # Skip reflection when no data tool was used (preserved behaviour, BR-5).
        state["reflection_count"] = state.get("reflection_count", 0) + 1
        return state

    def route(state: dict) -> str:
        if not state.get("used_data_tool"):
            return END
        if state.get("reflection_count", 0) >= min(state.get("max_reflections", 1), 3):
            return END
        return "reflection"

    g = StateGraph(dict)
    g.add_node("ground", ground)
    g.add_node("query_analyzer", query_analyzer)
    g.add_node("reflection", reflection)
    g.set_entry_point("ground")
    g.add_edge("ground", "query_analyzer")
    g.add_conditional_edges("query_analyzer", route, {"reflection": "reflection", END: END})
    g.add_edge("reflection", "query_analyzer")
    return g.compile()


@register("analytics.v1")
def analytics_module():
    return build_analytics_graph


async def run_analytics(deps: GraphDeps, inputs: dict) -> GraphOutcome:
    graph = build_analytics_graph(deps)
    final = await graph.ainvoke(dict(inputs))
    trace = [*final.get("trace", []), {"event": "run_completed"}]
    return GraphOutcome(final_text=final.get("draft", ""), usage=final.get("usage", {}),
                        trace=trace)
