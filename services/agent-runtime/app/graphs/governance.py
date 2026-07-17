"""governance agent (PRIORITY, ART-FR-040, US-10).

Runs autonomously under its AGENT PRINCIPAL (never a borrowed user identity).
Given drift/correction signals it decides whether to open a RETRAIN proposal
(a Temporal HITL workflow). The model summarises the evidence into the rationale;
the decision to propose is threshold-driven and auditable.
"""

from __future__ import annotations

import json

from langgraph.graph import END, StateGraph

from app.graphs.base import GraphDeps, GraphOutcome, WriteIntent, register

RETRAIN_TOOL_ID = "mlops.open_retrain"
RETRAIN_TOOL_VERSION = "1.0.0"

_SYS = (
    "You are Windrose's ML governance agent. Given drift metrics and human "
    "correction signals for a deployed claims model, write ONE concise sentence "
    "justifying whether a retrain is warranted. Respond with ONLY that sentence."
)


def build_governance_graph(deps: GraphDeps):
    async def assess(state: dict) -> dict:
        signals = state.get("signals", {})
        drift = float(signals.get("drift_score", 0.0))
        corrections = int(signals.get("correction_count", 0))
        threshold = float(state.get("drift_threshold", 0.3))
        state["should_retrain"] = drift >= threshold or corrections >= 20
        user = (f"Model: {state.get('model_urn')}\nDrift score: {drift}\n"
                f"Human corrections: {corrections}\nThreshold: {threshold}\n"
                f"Signals: {json.dumps(signals, default=str)[:800]}")
        result = await deps.llm.chat(
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": user}],
            tenant_id=state["tenant_id"], temperature=0.2, max_tokens=160)
        state["rationale"] = (result.content or "").strip()[:4000] or (
            f"Drift {drift} vs threshold {threshold}; {corrections} corrections.")
        state["usage"] = {"input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens, "model": result.model}
        return state

    async def propose(state: dict) -> dict:
        if state.get("should_retrain"):
            model_urn = state.get("model_urn", "wr:t:model:model/unknown")
            state["write_intent"] = WriteIntent(
                tool_id=RETRAIN_TOOL_ID, tool_version=RETRAIN_TOOL_VERSION,
                tier="write-proposal", side_effects="reversible",
                args={"model_urn": model_urn,
                      "reason": "drift_exceeded",
                      "drift_score": state.get("signals", {}).get("drift_score", 0.0)},
                rationale=state["rationale"],
                affected_urns=[model_urn],
                predicted_effect={"summary": f"Open retrain proposal for {model_urn}.",
                                  "reversibility": "reversible", "blast_radius": 1})
            state.setdefault("trace", []).append(
                {"event": "proposal_created", "tool_id": RETRAIN_TOOL_ID})
        return state

    g = StateGraph(dict)
    g.add_node("assess", assess)
    g.add_node("propose", propose)
    g.set_entry_point("assess")
    g.add_edge("assess", "propose")
    g.add_edge("propose", END)
    return g.compile()


@register("governance.v1")
def governance_module():
    return build_governance_graph


async def run_governance(deps: GraphDeps, inputs: dict) -> GraphOutcome:
    graph = build_governance_graph(deps)
    final = await graph.ainvoke(dict(inputs))
    txt = ("Retrain proposal opened." if final.get("should_retrain")
           else "No retrain warranted; drift within threshold.")
    return GraphOutcome(final_text=txt, write_intent=final.get("write_intent"),
                        usage=final.get("usage", {}), trace=final.get("trace", []))
