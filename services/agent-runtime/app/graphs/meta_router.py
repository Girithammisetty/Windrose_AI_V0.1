"""meta-router agent (ART-FR-040, §8.4 "Meta-agent router").

Classifies the user's request and delegates to the specialist agent whose
skill matches, reusing the SAME ``GraphDeps`` (real LLM, real memory, real
downstream readers) so the delegate runs exactly as if it had been invoked
directly — no mocked hand-off. The router never invents write authority: it
forwards the delegate's own ``GraphOutcome`` (including any WriteIntent)
unchanged, so whether a run becomes a Proposal is governed entirely by the
delegate's write mode, not by the router.

case-triage is excluded from the candidate set: it requires a case_id (the
chat route rejects a case-triage call without one), so a case-scoped copilot
should invoke it directly rather than through free-text routing.
"""

from __future__ import annotations

import json

from langgraph.graph import END, StateGraph

from app.graphs.base import GraphDeps, GraphOutcome, register

_CANDIDATES = [
    ("analytics", "Conversational analytics over the governed semantic layer. "
                  "Use for questions about data, counts, trends, metrics."),
    ("onboarding", "Proposes ingestion configs and column mappings for a new "
                   "data source. Use for requests to onboard, import, or load data."),
    ("model-training", "Proposes a training run (algorithm, hyperparameters, "
                        "features) for a dataset. Use for requests to train or "
                        "build a model."),
    ("inference", "Proposes a batch inference job with a registered model. Use "
                  "for requests to run, score, or predict with an existing model."),
    ("dashboard-designer", "Proposes a draft dashboard (title + charts) over "
                            "the semantic layer. Use for requests to design or "
                            "build a dashboard or report."),
    ("governance", "Assesses drift/correction signals and opens a retrain "
                   "proposal if warranted. Use for model-governance or drift "
                   "questions."),
]
_ALLOWED = {k for k, _ in _CANDIDATES}
_DEFAULT = "analytics"

_SYS = (
    "You are Windrose's meta-router. Given a user's request, choose the ONE "
    "specialist agent best suited to handle it from this list:\n"
    + "\n".join(f"- {k}: {d}" for k, d in _CANDIDATES)
    + "\n\nRespond with ONLY a JSON object: "
    '{"agent_key": "<one of the keys above>", "confidence": <0..1 number>, '
    '"rationale": "<one sentence>"}. If uncertain, choose "analytics".'
)

# Keys the router itself adds to state; everything else in the original input
# (tenant_id/query/workspace_id/case_id, plus any caller-supplied extra fields
# such as governance's signals/model_urn or onboarding's connection_id) is
# forwarded to the delegate UNCHANGED — the router narrows only WHICH agent
# runs, never WHAT it sees.
_ROUTER_OWNED_KEYS = {"target_agent_key", "routing_rationale", "usage", "trace",
                     "delegate_outcome"}


def build_meta_router_graph(deps: GraphDeps):
    async def classify(state: dict) -> dict:
        query = state.get("query", "") or ""
        result = await deps.llm.chat(
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": query}],
            tenant_id=state["tenant_id"],
            # ai-gateway's semantic cache matches on embedding similarity of the
            # full prompt (AIG-FR-040/BR-15). The classify system prompt is long
            # and near-identical across calls (it enumerates the fixed candidate
            # list every time) while the distinguishing signal is just the short
            # trailing user query — so at temperature<=0.2 (cache-eligible,
            # AIG-FR-042) two DIFFERENT routing questions can embed above the
            # 0.97 similarity threshold and the cache serves the FIRST call's
            # target for every later one, silently breaking routing. temperature
            # just above cache_max_temperature (0.2) is the gateway's documented
            # opt-out — the sanctioned way to force a live call per request.
            temperature=0.3, max_tokens=200,
            response_format={"type": "json_object"})
        try:
            parsed = json.loads(result.content or "{}")
        except (json.JSONDecodeError, TypeError):
            parsed = {}
        target = parsed.get("agent_key")
        if target not in _ALLOWED:
            target = _DEFAULT
        state["target_agent_key"] = target
        state["routing_rationale"] = str(parsed.get("rationale", ""))[:400]
        state["usage"] = {"input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens, "model": result.model}
        state.setdefault("trace", []).append(
            {"event": "routed", "target_agent_key": target,
             "rationale": state["routing_rationale"]})
        return state

    async def delegate(state: dict) -> dict:
        # Local import: app.graphs.__init__ populates RUNNERS by importing this
        # module, so a module-level `from app.graphs import RUNNERS` would be
        # circular. By the time this node executes, app.graphs has finished
        # importing and RUNNERS is fully populated.
        from app.graphs import RUNNERS

        target = state["target_agent_key"]
        _, runner = RUNNERS[target]
        delegate_inputs = {k: v for k, v in state.items() if k not in _ROUTER_OWNED_KEYS}
        state["delegate_outcome"] = await runner(deps, delegate_inputs)
        return state

    g = StateGraph(dict)
    g.add_node("classify", classify)
    g.add_node("delegate", delegate)
    g.set_entry_point("classify")
    g.add_edge("classify", "delegate")
    g.add_edge("delegate", END)
    return g.compile()


@register("meta_router.v1")
def meta_router_module():
    return build_meta_router_graph


async def run_meta_router(deps: GraphDeps, inputs: dict) -> GraphOutcome:
    graph = build_meta_router_graph(deps)
    final = await graph.ainvoke(dict(inputs))
    outcome: GraphOutcome | None = final.get("delegate_outcome")
    router_usage = final.get("usage", {}) or {}
    combined_usage = dict(router_usage)
    if outcome and outcome.usage:
        combined_usage["input_tokens"] = (
            router_usage.get("input_tokens", 0) + outcome.usage.get("input_tokens", 0))
        combined_usage["output_tokens"] = (
            router_usage.get("output_tokens", 0) + outcome.usage.get("output_tokens", 0))
        combined_usage["model"] = outcome.usage.get("model", router_usage.get("model"))
    target = final.get("target_agent_key", _DEFAULT)
    prefix = f"[routed to {target}] "
    final_text = prefix + ((outcome.final_text or "") if outcome else "")
    trace = list(final.get("trace", []))
    if outcome:
        trace.extend(outcome.trace)
    return GraphOutcome(
        final_text=final_text,
        write_intent=outcome.write_intent if outcome else None,
        usage=combined_usage,
        trace=trace,
        structured={"routed_to": target,
                    "rationale": final.get("routing_rationale", ""),
                    **((outcome.structured if outcome else {}) or {})},
        evidence=outcome.evidence if outcome else [],
    )
