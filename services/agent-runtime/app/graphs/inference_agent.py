"""batch-inference agent (ART-FR-040) — the governed
model-builder "run batch inference off a registered model" capability.

Proposal-mode LangGraph. Grounds on experiment-service (the registered model + its
PRODUCTION version + that version's declared input schema) and dataset-service (the
input dataset's current-version schema), plus workspace/tenant memory of prior
inference jobs, then validates dataset<->model feature compatibility and PROPOSES a
batch inference job as a WRITE INTENT for the ``inference.submit`` tool — never a
direct write. The runtime converts the intent into a Proposal requiring human
approval; on approve it executes via inference-service's real submit path under a
signed grant, producing an output dataset (a reversible side effect).

If the input dataset is INCOMPATIBLE with the model's feature contract, the agent
returns a plain-text explanation of the gap and emits NO proposal.

Real LangGraph StateGraph: ground -> check -> (propose | END).
"""

from __future__ import annotations

import json
import re
from typing import Any

from langgraph.graph import END, StateGraph

from app.adapters.memory import GroundingDegraded
from app.graphs.base import GraphDeps, GraphOutcome, WriteIntent, register

INFERENCE_TOOL_ID = "inference.submit"
INFERENCE_TOOL_VERSION = "1.0.0"

# Numeric widening ladder (mirrors inference-service schema_compat, INF-FR-002): a
# dataset column is usable where a model column is required if it is the same type
# or NARROWER on this ladder (the value widens up to the model type). Strings never
# coerce; matching is otherwise exact + case-sensitive.
_NUMERIC_RANK = {"integer": 0, "int": 0, "long": 1, "float": 2, "double": 3}
_CANON = {"int": "integer", "integer": "integer", "long": "long",
          "float": "float", "double": "double"}

# Query tokens that carry no selection signal for model/dataset name matching.
_STOP = {"run", "batch", "inference", "inferences", "with", "the", "a", "an", "on",
         "of", "for", "using", "use", "model", "models", "dataset", "datasets",
         "data", "latest", "newest", "recent", "production", "prod", "please",
         "score", "scoring", "predict", "predictions", "against", "over", "to"}

_SYS = (
    "You are Windrose's batch-inference agent. Given a registered model's production "
    "version, its declared input features, and an input dataset's schema plus a "
    "deterministic compatibility verdict, write ONE concise sentence justifying "
    "running (or not running) batch inference. Respond with ONLY that sentence."
)


def _canon_type(t: str | None) -> str | None:
    if t is None:
        return None
    t = str(t).strip().lower()
    return _CANON.get(t, t)


def _type_compatible(required: str | None, actual: str | None) -> bool:
    r, a = _canon_type(required), _canon_type(actual)
    if r is None or a is None:
        return r == a
    if r == a:
        return True
    if r in _NUMERIC_RANK and a in _NUMERIC_RANK:
        return _NUMERIC_RANK[a] <= _NUMERIC_RANK[r]
    return False


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", str(text).lower())
            if t not in _STOP and len(t) > 1]


def _score_name(name: str, query_tokens: list[str]) -> int:
    low = str(name).lower()
    return sum(1 for t in set(query_tokens) if t in low)


def _model_input_columns(input_schema: Any) -> list[dict]:
    """Normalise a model version's declared input schema into a list of
    ``{name, type, required}``. Accepts the MLflow-signature list form
    ``[{name, type, required?}]`` or a ``{col: type}`` mapping; ``None``/empty ->
    no declared feature contract (the model accepts any columns)."""
    cols: list[dict] = []
    if isinstance(input_schema, list):
        for c in input_schema:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            if not name:
                continue
            cols.append({"name": str(name),
                         "type": _canon_type(c.get("type")) or c.get("type"),
                         "required": bool(c.get("required", True))})
    elif isinstance(input_schema, dict):
        for name, spec in input_schema.items():
            if isinstance(spec, dict):
                cols.append({"name": str(name),
                             "type": _canon_type(spec.get("type")) or spec.get("type"),
                             "required": bool(spec.get("required", spec.get("nullable", True)
                                                       is False))})
            else:
                cols.append({"name": str(name), "type": _canon_type(spec) or spec,
                             "required": True})
    return cols


def _compatibility(model_cols: list[dict], dataset_schema: dict,
                   row_count: int | None) -> dict:
    """Deterministic dataset<->model feature-compatibility report (INF-FR-002)."""
    ds = dataset_schema if isinstance(dataset_schema, dict) else {}
    columns: list[dict] = []
    for col in model_cols:
        ds_col = ds.get(col["name"])
        if ds_col is None:
            columns.append({"name": col["name"], "required_type": col["type"],
                            "actual_type": None, "verdict": "missing"})
            continue
        actual = ds_col.get("type") if isinstance(ds_col, dict) else ds_col
        actual_nullable = bool(ds_col.get("nullable", True)) if isinstance(ds_col, dict) else True
        if not _type_compatible(col["type"], actual):
            columns.append({"name": col["name"], "required_type": col["type"],
                            "actual_type": _canon_type(actual) or actual,
                            "verdict": "type_mismatch"})
            continue
        if actual_nullable and col["required"]:
            columns.append({"name": col["name"], "required_type": col["type"],
                            "actual_type": _canon_type(actual) or actual,
                            "verdict": "nullable_mismatch"})
            continue
        columns.append({"name": col["name"], "required_type": col["type"],
                        "actual_type": _canon_type(actual) or actual, "verdict": "ok"})

    warnings: list[dict] = []
    extra = sorted(set(ds) - {c["name"] for c in model_cols})
    if extra:
        warnings.append({"code": "EXTRA_COLUMNS", "columns": extra})
    compatible = all(c["verdict"] == "ok" for c in columns)
    if row_count == 0:
        warnings.append({"code": "EMPTY_INPUT", "columns": []})
        compatible = False
    violations = [c for c in columns if c["verdict"] != "ok"]
    return {"compatible": compatible, "columns": columns, "warnings": warnings,
            "violations": violations, "row_count": row_count,
            "no_declared_contract": not model_cols}


def build_inference_graph(deps: GraphDeps):
    async def ground(state: dict) -> dict:
        auth = deps.obo_token or ""
        tenant = state["tenant_id"]
        qtokens = _tokens(state.get("query", ""))
        model: dict = {}
        versions: list[dict] = []
        dataset: dict = {}
        schema_info: dict = {"version_no": None, "schema": {}, "row_count": None}
        memories: list[dict] = []

        # (a) resolve the registered model (explicit model_id wins, else best
        #     name-match against the request), then its versions + stages.
        if deps.experiment_reader is not None:
            model_id = state.get("model_id")
            if not model_id:
                models = await deps.experiment_reader.list_models(
                    tenant_id=tenant, auth_token=auth)
                ranked = sorted(
                    models, key=lambda m: _score_name(m.get("name", ""), qtokens),
                    reverse=True)
                # prefer a name match; fall back to the sole/first model if none scores
                best = next((m for m in ranked
                             if _score_name(m.get("name", ""), qtokens) > 0), None)
                chosen = best or (ranked[0] if ranked else None)
                model_id = chosen.get("id") if chosen else None
            if model_id:
                got = await deps.experiment_reader.get_model(
                    tenant_id=tenant, model_id=model_id, auth_token=auth)
                model = got.get("model") or {}
                versions = got.get("versions") or []

        # (b) resolve the input dataset (explicit dataset_id/urn wins, else best
        #     name-match, newest first), then its current-version schema.
        if deps.dataset_reader is not None:
            dataset_id = state.get("dataset_id")
            if not dataset_id and state.get("input_dataset_urn"):
                dataset_id = str(state["input_dataset_urn"]).rsplit("/", 1)[-1]
            if not dataset_id:
                q = " ".join(qtokens) or None
                datasets = await deps.dataset_reader.list_datasets(
                    tenant_id=tenant, auth_token=auth, q=q)
                ranked = sorted(
                    datasets,
                    key=lambda d: (_score_name(d.get("name", ""), qtokens),
                                   d.get("created_at") or ""),
                    reverse=True)
                dataset = ranked[0] if ranked else {}
                dataset_id = dataset.get("id")
            else:
                # explicit id: still surface a name/urn if the catalog has it
                for d in await deps.dataset_reader.list_datasets(
                        tenant_id=tenant, auth_token=auth):
                    if d.get("id") == dataset_id:
                        dataset = d
                        break
            if dataset_id:
                schema_info = await deps.dataset_reader.get_schema(
                    tenant_id=tenant, dataset_id=dataset_id, auth_token=auth)
                dataset.setdefault("id", dataset_id)

        # (c) workspace+tenant memory of prior inference jobs (grounds the rationale;
        #     surfaced as GraphOutcome.evidence). Replay pins to a corpus snapshot.
        if deps.memory is not None:
            try:
                mq = (f"prior batch inference jobs for model "
                      f"{model.get('name', '')} {state.get('query', '')}")[:400]
                memories = await deps.memory.retrieve(
                    tenant_id=tenant, query=mq, auth_token=auth, top_k=5,
                    snapshot_ver=deps.memory_snapshot_ver)
            except GroundingDegraded as exc:
                memories = []
                state["grounding_degraded"] = {"status": exc.status_code,
                                               "source": "memory-service"}
                state.setdefault("trace", []).append(
                    {"event": "grounding_degraded", "source": "memory-service",
                     "status": exc.status_code})

        state["model"] = model
        state["versions"] = versions
        state["dataset"] = dataset
        state["dataset_schema"] = schema_info.get("schema") or {}
        state["dataset_version_no"] = schema_info.get("version_no")
        state["dataset_row_count"] = schema_info.get("row_count")
        state["memories"] = memories
        state.setdefault("trace", []).append(
            {"event": "tool_call_result", "tool_id": "experiment.model.get",
             "model": model.get("name"), "versions": len(versions),
             "dataset": dataset.get("name"),
             "dataset_columns": len(state["dataset_schema"]),
             "memories": len(memories)})
        return state

    async def check(state: dict) -> dict:
        model = state.get("model") or {}
        dataset = state.get("dataset") or {}
        versions = state.get("versions") or []
        # pick the PRODUCTION version (BR: only promoted models score in proposal mode)
        prod = next((v for v in versions if str(v.get("stage", "")).lower() == "production"),
                    None)

        if not model or not model.get("id"):
            state["blocked_reason"] = ("Could not resolve a registered model from the "
                                       "request. Name the model to run inference with.")
            state["compatible"] = False
            return state
        if not dataset or not dataset.get("id"):
            state["blocked_reason"] = (f"Resolved model '{model.get('name')}' but could "
                                       "not resolve an input dataset from the request.")
            state["compatible"] = False
            return state
        if prod is None:
            stages = ", ".join(sorted({str(v.get("stage")) for v in versions})) or "none"
            state["blocked_reason"] = (
                f"Model '{model.get('name')}' has no version in PRODUCTION "
                f"(stages present: {stages}); promote a version before running "
                "batch inference.")
            state["compatible"] = False
            return state

        model_cols = _model_input_columns(prod.get("input_schema"))
        report = _compatibility(model_cols, state.get("dataset_schema") or {},
                                state.get("dataset_row_count"))
        state["chosen_version"] = prod
        state["model_cols"] = model_cols
        state["compatibility"] = report
        state["compatible"] = bool(report["compatible"])

        # Incompatible input: block with a DETERMINISTIC explanation of the gap
        # (independent of the LLM rationale) so the answer names the exact feature
        # issues and no proposal is emitted.
        if not report["compatible"]:
            state["blocked_reason"] = _explain_incompatible(
                model.get("name"), prod.get("version"), dataset.get("name"), report)

        # LLM rationale grounded in the resolved facts (deterministic fallback if the
        # gateway is unavailable — the verdict itself is always deterministic).
        mems = [m.get("content", m) for m in state.get("memories", [])]
        verdict = "COMPATIBLE" if report["compatible"] else "INCOMPATIBLE"
        user = (
            f"Request: {state.get('query', '')}\n"
            f"Model: {model.get('name')} v{prod.get('version')} (stage=production)\n"
            f"Model input features: {json.dumps(model_cols, default=str)[:800]}\n"
            f"Dataset: {dataset.get('name')} "
            f"(v{state.get('dataset_version_no')}, rows={state.get('dataset_row_count')})\n"
            f"Dataset schema: {json.dumps(state.get('dataset_schema'), default=str)[:800]}\n"
            f"Compatibility verdict: {verdict}; "
            f"violations={json.dumps(report['violations'], default=str)[:500]}\n"
            f"Prior inference-job memory: {json.dumps(mems, default=str)[:600]}\n"
            "Write the one-sentence justification now."
        )
        try:
            result = await deps.llm.chat(
                messages=[{"role": "system", "content": _SYS},
                          {"role": "user", "content": user}],
                tenant_id=state["tenant_id"], temperature=0.2, max_tokens=160)
            state["usage"] = {"input_tokens": result.input_tokens,
                              "output_tokens": result.output_tokens,
                              "model": result.model,
                              "deployment": getattr(result, "deployment", None)}
            rationale = (result.content or "").strip()
        except Exception as exc:  # noqa: BLE001 — never fail the run on a rationale
            state.setdefault("trace", []).append(
                {"event": "llm_degraded", "error": str(exc)[:200]})
            rationale = ""
        if not rationale:
            if report["compatible"]:
                rationale = (
                    f"Input dataset '{dataset.get('name')}' is compatible with "
                    f"'{model.get('name')}' v{prod.get('version')}"
                    + (" (model declares no explicit feature contract)."
                       if report["no_declared_contract"] else
                       f" across {len(model_cols)} declared features."))
            else:
                rationale = (f"Input dataset '{dataset.get('name')}' is incompatible "
                             f"with '{model.get('name')}' v{prod.get('version')}: "
                             f"{len(report['violations'])} feature issue(s).")
        state["rationale"] = rationale[:4000]
        state.setdefault("trace", []).append(
            {"event": "reflection", "iteration": 0, "verdict": verdict,
             "model_version": prod.get("version")})
        return state

    async def propose(state: dict) -> dict:
        model = state["model"]
        dataset = state["dataset"]
        version = int(state["chosen_version"]["version"])
        tenant = state["tenant_id"]
        model_id = model["id"]
        # canonical URNs inference-service's submit path resolves (INF-FR-001/002):
        # model_version/<id>@<n> and dataset/<id>.
        mv_urn = f"wr:{tenant}:experiment:model_version/{model_id}@{version}"
        ds_urn = (dataset.get("urn")
                  or f"wr:{tenant}:dataset:dataset/{dataset['id']}")
        out_name = _output_name(model.get("name", "model"), version)
        args: dict[str, Any] = {
            "model_id": model_id,
            "model_version": version,
            "model_version_urn": mv_urn,
            "input_dataset": ds_urn,
            "input_dataset_urn": ds_urn,
            "output_dataset_name": out_name,
        }
        # Carry workspace_id so the propose-time caller-gate (ART-FR-044) can
        # evaluate the workspace-scoped inference.job.create action.
        if state.get("workspace_id"):
            args["workspace_id"] = state["workspace_id"]
        report = state.get("compatibility") or {}
        state["write_intent"] = WriteIntent(
            tool_id=INFERENCE_TOOL_ID, tool_version=INFERENCE_TOOL_VERSION,
            tier="write-proposal", side_effects="reversible", args=args,
            rationale=state["rationale"],
            affected_urns=[mv_urn, ds_urn],
            required_action="inference.job.create",
            predicted_effect={
                "summary": (f"Score dataset '{dataset.get('name')}' with "
                            f"'{model.get('name')}' v{version} (production) -> new "
                            f"output dataset '{out_name}'."),
                "reversibility": "reversible", "blast_radius": 1,
                "compatibility": {"compatible": report.get("compatible"),
                                  "violations": report.get("violations", []),
                                  "warnings": report.get("warnings", []),
                                  "row_count": report.get("row_count")}})
        state.setdefault("trace", []).append(
            {"event": "proposal_created", "tool_id": INFERENCE_TOOL_ID})
        return state

    def route(state: dict) -> str:
        return "propose" if state.get("compatible") else END

    g = StateGraph(dict)
    g.add_node("ground", ground)
    g.add_node("check", check)
    g.add_node("propose", propose)
    g.set_entry_point("ground")
    g.add_edge("ground", "check")
    g.add_conditional_edges("check", route, {"propose": "propose", END: END})
    g.add_edge("propose", END)
    return g.compile()


def _explain_incompatible(model_name: str | None, version: Any, dataset_name: str | None,
                          report: dict) -> str:
    parts = []
    for v in report.get("violations", []):
        if v["verdict"] == "missing":
            parts.append(f"missing feature '{v['name']}' ({v['required_type']})")
        elif v["verdict"] == "type_mismatch":
            parts.append(f"'{v['name']}' is {v['actual_type']} but the model "
                         f"requires {v['required_type']}")
        elif v["verdict"] == "nullable_mismatch":
            parts.append(f"required feature '{v['name']}' is nullable in the dataset")
    if any(w["code"] == "EMPTY_INPUT" for w in report.get("warnings", [])):
        parts.append("the input dataset is empty")
    detail = "; ".join(parts) or "the schemas do not match"
    return (f"Dataset '{dataset_name}' is INCOMPATIBLE with model '{model_name}' "
            f"v{version}: {detail}. No inference job proposed — fix the input schema "
            "or pick a compatible dataset.")


def _output_name(model_name: str, version: int) -> str:
    base = re.sub(r"[^a-zA-Z0-9_\- ]+", "-", f"{model_name}-v{version}-scores")
    base = base.strip() or f"model-v{version}-scores"
    if len(base) < 3:
        base = f"{base}-scores"
    return base[:120]


@register("inference.v1")
def inference_module():
    return build_inference_graph


async def run_inference(deps: GraphDeps, inputs: dict) -> GraphOutcome:
    graph = build_inference_graph(deps)
    final = await graph.ainvoke(dict(inputs))
    model = final.get("model") or {}
    dataset = final.get("dataset") or {}
    if final.get("write_intent") is not None:
        v = final.get("chosen_version", {}).get("version")
        final_text = (f"Proposed batch inference: '{model.get('name')}' v{v} "
                      f"(production) on dataset '{dataset.get('name')}'.")
    else:
        final_text = final.get("blocked_reason") or final.get("rationale") or (
            "Could not propose a batch inference job.")
    return GraphOutcome(
        final_text=final_text,
        write_intent=final.get("write_intent"),
        usage=final.get("usage", {}),
        trace=final.get("trace", []),
        structured={"model": model.get("name"),
                    "model_version": (final.get("chosen_version") or {}).get("version"),
                    "dataset": dataset.get("name"),
                    "compatibility": final.get("compatibility", {})},
        evidence=final.get("memories", []))
