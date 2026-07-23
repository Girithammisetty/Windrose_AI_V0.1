"""data-pipeline-builder agent (BRD 62) — the GOVERNED, proposal-mode no-code
DATA-PREP pipeline builder. `model_training` builds *training* pipelines; nothing
built *data-prep / feature-engineering* pipelines, so this is a genuinely new task
type (the agent complement to BRD 62's local operator-execution engine).

It grounds on (a) the live operator catalog (pipeline-orchestrator
`GET /components`), (b) the source dataset, and (c) workspace/tenant memory of prior
prep decisions; has the REAL model (ai-gateway → Ollama) choose an ORDERED list of
operators (+ their params) grounded in that catalog; and PROPOSES creating the
pipeline as a WRITE INTENT (`pipeline.template.create`) — never a direct write. The
agent wires the chosen operators into a validated LINEAR DAG (read-from-warehouse →
op₁ → … → opₙ → write-to-warehouse) deterministically, so the emitted definition
always passes the same DAG validator a UI submission runs. On approval, tool-plane
executes the create under a signed grant, materializing a real pipeline template.

Real LangGraph StateGraph: ground → plan → propose.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langgraph.graph import END, StateGraph

from app.adapters.memory import GroundingDegraded
from app.graphs.base import GraphDeps, GraphOutcome, WriteIntent, register
from app.prompts import system_prompt

CREATE_TOOL_ID = "pipeline.template.create"
CREATE_TOOL_VERSION = "1.0.0"

_READ = "read-from-warehouse"
_WRITE = "write-to-warehouse"
_DATA_PREP = 1  # catalog component_type for data-prep operators

_SYS = system_prompt("data_pipeline_builder.system")


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _dataset_urn(inputs: dict, tenant_id: str) -> str:
    ref = inputs.get("dataset_urn") or inputs.get("dataset_ref")
    if isinstance(ref, str) and ref.startswith("wr:"):
        return ref
    slug = re.sub(r"[^a-z0-9_-]+", "-", str(inputs.get("dataset") or "claims").lower())
    return f"wr:{tenant_id}:dataset:dataset/{slug or 'claims'}"


def _build_definition(operators: list[dict], dataset_urn: str) -> dict:
    """Wire the chosen operators into a validated LINEAR DAG: read → ops → write.
    Deterministic aliasing + edge chaining, so the emitted definition is always a
    well-formed DAG the orchestrator validator accepts."""
    nodes: list[dict] = [{"alias": "read_1", "component": _READ,
                          "parameters": {"dataset": dataset_urn}}]
    edges: list[dict] = []
    prev = "read_1"
    for i, op in enumerate(operators, start=1):
        alias = f"op_{i}"
        nodes.append({"alias": alias, "component": op["component"],
                      "parameters": op.get("parameters") or {}})
        edges.append({"from": f"{prev}.out", "to": alias})
        prev = alias
    nodes.append({"alias": "write_1", "component": _WRITE, "parameters": {}})
    edges.append({"from": f"{prev}.out", "to": "write_1"})
    return {"nodes": nodes, "edges": edges}


def build_data_pipeline_builder_graph(deps: GraphDeps):
    async def ground(state: dict) -> dict:
        tenant_id = state["tenant_id"]
        query = state.get("query", "") or ""
        token = deps.obo_token or ""

        components: list[dict] = []
        if deps.pipeline_reader is not None and hasattr(deps.pipeline_reader, "list_components"):
            components = await deps.pipeline_reader.list_components(
                tenant_id=tenant_id, auth_token=token)
        # The operator names the model may choose from (data-prep components only).
        op_names = [c.get("name") for c in components
                    if isinstance(c, dict) and c.get("component_type") == _DATA_PREP
                    and c.get("name")]
        state["operator_catalog"] = op_names
        if not op_names:
            state.setdefault("trace", []).append(
                {"event": "grounding_degraded", "source": "pipeline-orchestrator",
                 "detail": "operator catalog unavailable"})

        memories: list[dict] = []
        if deps.memory is not None:
            try:
                memories = await deps.memory.retrieve(
                    tenant_id=tenant_id, query=f"data prep pipeline {query}".strip(),
                    auth_token=token, top_k=5, snapshot_ver=deps.memory_snapshot_ver)
            except GroundingDegraded as exc:
                state["grounding_degraded"] = {"status": exc.status_code,
                                               "source": "memory-service"}
                state.setdefault("trace", []).append(
                    {"event": "grounding_degraded", "source": "memory-service",
                     "status": exc.status_code})
        state["memories"] = memories
        state.setdefault("trace", []).append(
            {"event": "tool_call_result", "tool_id": "pipeline.components.list",
             "operators": len(op_names), "memories": len(memories)})
        return state

    async def plan(state: dict) -> dict:
        catalog = state.get("operator_catalog", [])
        mems = [m.get("content", m) for m in state.get("memories", [])]
        user = (
            f"Data-prep request: {state.get('query', '')}\n"
            f"Operator catalog (choose ONLY from these): {json.dumps(catalog)}\n"
            f"Similar prior prep decisions: {json.dumps(mems, default=str)[:800]}\n"
            "Choose the ordered operators + parameters now."
        )
        result = await deps.llm.chat(
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": user}],
            tenant_id=state["tenant_id"],
            response_format={"type": "json_object"},
            temperature=0.2, max_tokens=500)
        state["model_output"] = result.content
        state["usage"] = {"input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens,
                          "model": result.model, "deployment": result.deployment}
        state["plan"] = _normalise_plan(_extract_json(result.content), catalog, state)
        state.setdefault("trace", []).append(
            {"event": "reflection", "iteration": 0, "model": result.model})
        return state

    async def propose(state: dict) -> dict:
        p = state["plan"]
        tenant_id = state["tenant_id"]
        dataset_urn = _dataset_urn(state, tenant_id)
        definition = _build_definition(p["operators"], dataset_urn)
        args = {
            "name": p["name"],
            "pipeline_type": "data_prep",
            "definition": definition,
            "workspace_id": state.get("workspace_id"),
        }
        op_summary = " → ".join(o["component"] for o in p["operators"]) or "(no operators)"
        slug = re.sub(r"[^a-z0-9]+", "-", p["name"].lower()).strip("-") or "data-prep"
        state["write_intent"] = WriteIntent(
            tool_id=CREATE_TOOL_ID, tool_version=CREATE_TOOL_VERSION,
            tier="write-proposal", side_effects="reversible", args=args,
            rationale=p["rationale"],
            affected_urns=[f"wr:{tenant_id}:pipeline:data_prep/{slug}"],
            required_action="pipeline.template.create",
            predicted_effect={
                "summary": (f"Create a data-prep pipeline '{p['name']}' on {dataset_urn}: "
                            f"{_READ} → {op_summary} → {_WRITE}."),
                "reversibility": "reversible", "blast_radius": 1})
        state.setdefault("trace", []).append(
            {"event": "proposal_created", "tool_id": CREATE_TOOL_ID})
        return state

    g = StateGraph(dict)
    g.add_node("ground", ground)
    g.add_node("plan", plan)
    g.add_node("propose", propose)
    g.set_entry_point("ground")
    g.add_edge("ground", "plan")
    g.add_edge("plan", "propose")
    g.add_edge("propose", END)
    return g.compile()


def _normalise_plan(parsed: dict, catalog: list[str], state: dict) -> dict:
    """Keep ONLY operators whose component is in the catalog (fail safe: an unknown
    operator is dropped, never emitted), coerce params to objects, and always return
    a valid, non-empty name + rationale."""
    allowed = set(catalog)
    ops_in = parsed.get("operators") if isinstance(parsed.get("operators"), list) else []
    operators: list[dict] = []
    for op in ops_in:
        if not isinstance(op, dict):
            continue
        comp = op.get("component")
        if comp in allowed or (not allowed and isinstance(comp, str)):
            params = op.get("parameters")
            operators.append({"component": comp,
                              "parameters": params if isinstance(params, dict) else {}})
    name = parsed.get("name")
    name = str(name).strip()[:80] if isinstance(name, str) and name.strip() else "Data prep pipeline"
    rationale = parsed.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        rationale = (f"Composed a {len(operators)}-operator data-prep pipeline from the "
                     f"operator catalog for the request.")
    return {"name": name, "operators": operators, "rationale": rationale.strip()[:4000]}


@register("data_pipeline_builder.v1")
def data_pipeline_builder_module():
    return build_data_pipeline_builder_graph


async def run_data_pipeline_builder(deps: GraphDeps, inputs: dict) -> GraphOutcome:
    graph = build_data_pipeline_builder_graph(deps)
    final = await graph.ainvoke(dict(inputs))
    p = final["plan"]
    return GraphOutcome(
        final_text=(f"Proposed data-prep pipeline '{p['name']}' "
                    f"({len(p['operators'])} operators)."),
        write_intent=final.get("write_intent"),
        usage=final.get("usage", {}),
        trace=final.get("trace", []),
        structured={"name": p["name"],
                    "operators": [o["component"] for o in p["operators"]]},
        evidence=final.get("memories", []))
