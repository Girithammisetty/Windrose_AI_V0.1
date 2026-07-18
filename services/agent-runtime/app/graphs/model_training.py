"""model-training agent (PRIORITY, ART-FR-040) — the REFERENCE proposal-mode graph.
Model-builder's "choose algorithm template -> fill
params -> launch training run" as a GOVERNED, proposal-mode LangGraph agent. It
grounds on (a) the pipeline-orchestrator algorithm-template catalog + the chosen
algorithm's parameter schema, (b) prior MLflow experiment history for that
algorithm (experiment-service), and (c) workspace/tenant memory of prior training
decisions; has the REAL model (ai-gateway -> Ollama) fill the pipeline-template
parameters (label column, algorithm hyperparameters, feature columns) grounded in
that schema + history; and PROPOSES the training run as a WRITE INTENT — never a
direct write. The runtime converts the intent into a Proposal requiring human
approval; on approve tool-plane executes ``pipeline.template.create_from_algorithm``
(mode=train) under a signed grant, instantiating + launching a training pipeline.

(As with the triage copilot, the grounding reads are governed service reads; in
the platform target they are tool-plane read tools — the algorithm-template
catalog / ``experiment.runs.search``; direct governed read clients are used here
for the grounding step and documented as such.)

Real LangGraph StateGraph: ground -> plan -> propose.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langgraph.graph import END, StateGraph

from app.adapters.memory import GroundingDegraded
from app.domain.urn import pipeline_training_urn
from app.graphs.base import GraphDeps, GraphOutcome, WriteIntent, register
from app.prompts import system_prompt

# The single self-contained governed write: instantiate a train-mode pipeline from
# the algorithm with the filled params (pipeline-orchestrator McpFacade
# WRITE_PROPOSAL_TOOLS). ``params`` carries BOTH the hyperparameters (filled into
# the model component) AND label_column (instantiate_pipeline reads it for the
# run parameters), so one proposal fully captures the "fill params + train" action.
TRAINING_TOOL_ID = "pipeline.template.create_from_algorithm"
TRAINING_TOOL_VERSION = "1.0.0"

# Keyword -> catalog algorithm name for common NL phrasings (used only when the
# request doesn't name a catalog algorithm verbatim).
_ALGO_HINTS = {
    "xgboost": "xgboost", "xg boost": "xgboost", "xgb": "xgboost",
    "random forest": "random_forest", "randomforest": "random_forest",
    "logistic": "logistic_regression", "light gbm": "light_gbm",
    "lightgbm": "light_gbm", "lgbm": "light_gbm", "gradient boost": "light_gbm",
    "decision tree": "decision_tree", "naive bayes": "naive_bayes",
    "knn": "knn", "nearest neighbor": "knn", "svm": "svm",
    "support vector": "svm", "linear regression": "linear_regression",
}
_DEFAULT_ALGORITHM = "random_forest"

_SYS = system_prompt("model_training.system")


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _resolve_algorithm(inputs: dict, query: str, catalog_names: list[str]) -> str:
    """Deterministically resolve the algorithm: explicit input > a catalog name
    named verbatim in the request > a known keyword phrasing > safe default."""
    names = set(catalog_names)
    req = str(inputs.get("algorithm") or "").strip().lower()
    if req and (req in names or not names):
        return req
    q = (query or "").lower()
    for n in catalog_names:
        if n in q:
            return n
    for phrase, name in _ALGO_HINTS.items():
        if phrase in q and (name in names or not names):
            return name
    if _DEFAULT_ALGORITHM in names or not names:
        return _DEFAULT_ALGORITHM
    return catalog_names[0]


def _coerce_hyperparameters(schema: dict, filled: dict) -> dict:
    """Keep ONLY schema-declared params, coerce types, clamp to [min,max], and
    fall back to the schema default — so the emitted args validate identically to
    a UI submission."""
    out: dict[str, Any] = {}
    for name, spec in (schema or {}).items():
        if not isinstance(spec, dict):
            continue
        val = filled.get(name, spec.get("default"))
        if val is None:
            continue
        try:
            if spec.get("type") == "int":
                val = int(round(float(val)))
            elif spec.get("type") == "number":
                val = float(val)
        except (TypeError, ValueError):
            val = spec.get("default")
            if val is None:
                continue
        lo, hi = spec.get("minimum"), spec.get("maximum")
        if isinstance(val, (int, float)):
            if isinstance(lo, (int, float)):
                val = max(val, lo)
            if isinstance(hi, (int, float)):
                val = min(val, hi)
        out[name] = val
    return out


def _dataset_urn(inputs: dict, tenant_id: str) -> str:
    ref = inputs.get("dataset_urn") or inputs.get("dataset_ref")
    if isinstance(ref, str) and ref.startswith("wr:"):
        return ref
    slug = re.sub(r"[^a-z0-9_-]+", "-", str(inputs.get("dataset") or "claims").lower())
    return f"wr:{tenant_id}:dataset:dataset/{slug or 'claims'}"


def build_model_training_graph(deps: GraphDeps):
    async def ground(state: dict) -> dict:
        tenant_id = state["tenant_id"]
        query = state.get("query", "") or ""
        token = deps.obo_token or ""

        catalog: list[dict] = []
        if deps.pipeline_reader is not None:
            catalog = await deps.pipeline_reader.list_algorithms(
                tenant_id=tenant_id, auth_token=token)
        names = [a.get("name") for a in catalog if isinstance(a, dict) and a.get("name")]
        if not catalog:
            state.setdefault("trace", []).append(
                {"event": "grounding_degraded", "source": "pipeline-orchestrator",
                 "detail": "algorithm catalog unavailable"})

        algorithm = _resolve_algorithm(state, query, names)
        state["algorithm"] = algorithm

        algo_detail: dict = {}
        if deps.pipeline_reader is not None:
            algo_detail = await deps.pipeline_reader.get_algorithm(
                tenant_id=tenant_id, algorithm=algorithm, auth_token=token)
        state["algo_schema"] = algo_detail.get("parameters") or {}
        state["algo_label"] = algo_detail.get("label") or algorithm

        history: list[dict] = []
        if deps.experiment_reader is not None:
            history = await deps.experiment_reader.best_runs(
                tenant_id=tenant_id, algorithm=algorithm, auth_token=token, limit=5)
        state["history"] = history

        memories: list[dict] = []
        if deps.memory is not None:
            mem_query = f"model training {algorithm} {query}".strip()
            try:
                memories = await deps.memory.retrieve(
                    tenant_id=tenant_id, query=mem_query, auth_token=token, top_k=5,
                    snapshot_ver=deps.memory_snapshot_ver)
            except GroundingDegraded as exc:
                memories = []
                state["grounding_degraded"] = {"status": exc.status_code,
                                               "source": "memory-service"}
                state.setdefault("trace", []).append(
                    {"event": "grounding_degraded", "source": "memory-service",
                     "status": exc.status_code})
        state["memories"] = memories

        state.setdefault("trace", []).append(
            {"event": "tool_call_result", "tool_id": "pipeline.algorithm.read",
             "algorithm": algorithm, "schema_params": len(state["algo_schema"]),
             "history_runs": len(history), "memories": len(memories)})
        return state

    async def plan(state: dict) -> dict:
        schema = state["algo_schema"]
        hist = [{"metrics": h.get("metrics"), "params": h.get("params"),
                 "status": h.get("status")} for h in state.get("history", [])][:5]
        mems = [m.get("content", m) for m in state.get("memories", [])]
        user = (
            f"Training request: {state.get('query', '')}\n"
            f"Algorithm: {state['algorithm']} ({state.get('algo_label')})\n"
            f"Parameter schema (JSON): {json.dumps(schema, default=str)[:1200]}\n"
            f"Prior runs for this algorithm (JSON): {json.dumps(hist, default=str)[:1000]}\n"
            f"Similar prior training decisions: {json.dumps(mems, default=str)[:800]}\n"
            "Fill the template parameters now."
        )
        result = await deps.llm.chat(
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": user}],
            tenant_id=state["tenant_id"],
            response_format={"type": "json_object"},
            temperature=0.2, max_tokens=400)
        parsed = _extract_json(result.content)
        state["model_output"] = result.content
        state["usage"] = {"input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens,
                          "model": result.model, "deployment": result.deployment}
        state["plan"] = _normalise_plan(parsed, schema, state)
        state.setdefault("trace", []).append(
            {"event": "reflection", "iteration": 0, "model": result.model})
        return state

    async def propose(state: dict) -> dict:
        p = state["plan"]
        tenant_id = state["tenant_id"]
        algorithm = state["algorithm"]
        dataset_urn = _dataset_urn(state, tenant_id)
        params: dict[str, Any] = {**p["hyperparameters"], "label_column": p["label_column"]}
        if p.get("feature_columns"):
            params["feature_columns"] = p["feature_columns"]
        args = {
            "algorithm": algorithm,
            "mode": "train",
            "dataset_refs": {"TRAIN": dataset_urn},
            "params": params,
            "workspace_id": state.get("workspace_id"),
            "name": f"{state.get('algo_label', algorithm)} train — predict {p['label_column']}",
        }
        hp_summary = ", ".join(f"{k}={v}" for k, v in p["hyperparameters"].items()) or "defaults"
        state["write_intent"] = WriteIntent(
            tool_id=TRAINING_TOOL_ID, tool_version=TRAINING_TOOL_VERSION,
            tier="write-proposal", side_effects="reversible", args=args,
            rationale=p["rationale"],
            affected_urns=[pipeline_training_urn(tenant_id, algorithm)],
            required_action="pipeline.run.create",
            predicted_effect={
                "summary": (f"Launch a train-mode {algorithm} pipeline on {dataset_urn} "
                            f"to predict '{p['label_column']}' ({hp_summary})."),
                "reversibility": "reversible", "blast_radius": 1})
        state.setdefault("trace", []).append(
            {"event": "proposal_created", "tool_id": TRAINING_TOOL_ID})
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


def _normalise_plan(parsed: dict, schema: dict, state: dict) -> dict:
    hyper = _coerce_hyperparameters(
        schema, parsed.get("hyperparameters") if isinstance(
            parsed.get("hyperparameters"), dict) else {})
    label = parsed.get("label_column")
    label = str(label).strip() if isinstance(label, str) and label.strip() else "label"
    label = re.sub(r"[^A-Za-z0-9_]+", "_", label)[:64] or "label"
    feats = parsed.get("feature_columns")
    if isinstance(feats, list):
        feats = [str(f) for f in feats if isinstance(f, str) and f.strip()] or None
    else:
        feats = None
    rationale = parsed.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        rationale = (f"Grounded {state['algorithm']} plan: fills the template schema "
                     f"({len(hyper)} params) to predict '{label}'.")
    return {"hyperparameters": hyper, "label_column": label, "feature_columns": feats,
            "rationale": rationale.strip()[:4000]}


@register("model_training.v1")
def model_training_module():
    return build_model_training_graph


async def run_model_training(deps: GraphDeps, inputs: dict) -> GraphOutcome:
    graph = build_model_training_graph(deps)
    final = await graph.ainvoke(dict(inputs))
    p = final["plan"]
    return GraphOutcome(
        final_text=(f"Proposed training run: {final['algorithm']} to predict "
                    f"'{p['label_column']}' ({len(p['hyperparameters'])} params filled)."),
        write_intent=final.get("write_intent"),
        usage=final.get("usage", {}),
        trace=final.get("trace", []),
        structured={"algorithm": final["algorithm"], **p},
        evidence=final.get("memories", []))
