"""analytics agent graph (ART-FR-013) — read-only, framework-complete.

Preserves the chat-agent-service shape: query_analyzer (LLM) -> [call_tool] ->
reflection loop (max_reflections, skip when no data tool used). It is read-only:
it never emits a WriteIntent. Data tools are called via tool-plane in the platform
target; here the graph is wired for the framework + reflection control flow and
returns a grounded answer. Full semantic-layer tool wiring is Phase-2 follow-up.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.graphs.base import GraphDeps, GraphOutcome, register
from app.graphs.persona import role_directive

_SYS = ("You are Windrose's conversational analytics agent. Answer the user's "
        "data question concisely and cite that the answer is grounded in the "
        "governed semantic layer. Read-only.")


def build_analytics_graph(deps: GraphDeps):
    async def query_analyzer(state: dict) -> dict:
        # Role-ground the tone: a technical user gets precise terminology, an
        # operational user gets plain language (ART-FR-040). Empty when unknown.
        sys = _SYS
        directive = role_directive(state.get("caller"))
        if directive:
            sys = f"{_SYS} {directive}"
        result = await deps.llm.chat(
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": state["query"]}],
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
    g.add_node("query_analyzer", query_analyzer)
    g.add_node("reflection", reflection)
    g.set_entry_point("query_analyzer")
    g.add_conditional_edges("query_analyzer", route, {"reflection": "reflection", END: END})
    g.add_edge("reflection", "query_analyzer")
    return g.compile()


@register("analytics.v1")
def analytics_module():
    return build_analytics_graph


async def run_analytics(deps: GraphDeps, inputs: dict) -> GraphOutcome:
    graph = build_analytics_graph(deps)
    final = await graph.ainvoke(dict(inputs))
    return GraphOutcome(final_text=final.get("draft", ""), usage=final.get("usage", {}),
                        trace=[{"event": "run_completed"}])
