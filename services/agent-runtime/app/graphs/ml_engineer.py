"""ml-engineer agent (BRD 52) — autonomous train -> evaluate -> PROPOSE promotion.

Automates the mechanical 8/10ths of the data-scientist loop and keeps the
judgment human: it inspects a governed dataset's real schema, plans candidate
algorithms from the pipeline-orchestrator catalog, LAUNCHES training runs
itself (sandboxed, reversible, authorized by the OBO user's own
``pipeline.template.create`` — the same route/authz/audit as a UI click),
polls them to completion, compares candidates on metrics read verbatim from
the run payloads (the LLM never writes a number), and then emits the ONE
consequential action as a WriteIntent: ``experiment.model.promote`` — which
the runtime converts to a human proposal, and whose eventual execution only
creates a PENDING promotion that experiment-service's own four-eyes governs
(two-layer HITL by construction, MLE-FR-020).

Failure posture (BRD 52 BR-5): any unusable input or failed step ends in an
honest report of what ran / what failed / what artifacts exist — never a
fabricated metric, never a promotion proposal without evaluated evidence.

Real LangGraph StateGraph: ground -> plan -> train -> propose.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from langgraph.graph import END, StateGraph

from app.graphs.base import GraphDeps, GraphOutcome, WriteIntent, register

PROMOTE_TOOL_ID = "experiment.model.promote"
PROMOTE_TOOL_VERSION = "1.0.0"

# Candidate preference order: runnable, supervised, and proven in this
# platform's catalog. The plan intersects this with the live catalog.
_CANDIDATE_PREFERENCE = [
    "xgboost", "random_forest", "light_gbm", "logistic_regression",
    "decision_tree", "linear_regression",
]
_MAX_CANDIDATES = 3
_POLL_INTERVAL_S = 5.0
_POLL_MAX_TRIES = 60  # per run: 60 × 5s = 5 minutes
_TERMINAL = {"succeeded", "failed", "error", "cancelled"}
# The experiment registry mirrors a finished training run's registered model
# asynchronously (MLflow -> experiment-service via Kafka), so the version may lag
# the run reaching "succeeded" by a moment. Poll for it before giving up rather
# than failing the first time it is not yet there (MLE-FR-031).
_RESOLVE_MAX_TRIES = 10
_RESOLVE_INTERVAL_S = 3.0  # up to ~30s for the registry mirror to catch up

# (metric, better) preference for ranking candidates — higher-is-better first,
# loss-style metrics as fallback. Ranking is DETERMINISTIC (BR-5).
_METRIC_PREFERENCE: list[tuple[str, bool]] = [
    ("f1", True), ("accuracy", True), ("auc", True), ("roc_auc", True),
    ("r2", True), ("rmse", False), ("mae", False),
]

_SYS = (
    "You are Windrose's ml-engineer agent. Given a dataset schema, a target "
    "column, and the parameter schemas of candidate algorithms, fill sensible "
    "hyperparameters for EACH candidate. Respond with ONLY a JSON object: "
    '{"candidates": {algorithm_name: {schema_param: value, ...}, ...}, '
    '"feature_columns": array of feature column names or null for all-but-target, '
    '"rationale": one concise sentence justifying the choices}. Use ONLY '
    "parameter names present in each algorithm's schema and values within their "
    "min/max. No prose outside JSON."
)


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _coerce_hyperparameters(schema: dict, filled: dict) -> dict:
    """Schema-validated params only: keep declared names, coerce types, clamp
    to [min,max], fall back to defaults (mirrors model_training's guard)."""
    out: dict[str, Any] = {}
    for name, spec in (schema or {}).items():
        if not isinstance(spec, dict):
            continue
        val = (filled or {}).get(name, spec.get("default"))
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


def _fail(state: dict, report: str) -> dict:
    state["failed"] = True
    state["report"] = report
    state.setdefault("trace", []).append({"event": "honest_failure", "detail": report})
    return state


def _pick_primary_metric(candidates: list[dict]) -> tuple[str, bool] | None:
    """The first preferred metric present on EVERY succeeded candidate, else
    the first numeric metric they share, else None."""
    metric_sets = [set((c.get("metrics") or {}).keys())
                   for c in candidates if c.get("status") == "succeeded"]
    if not metric_sets:
        return None
    shared = set.intersection(*metric_sets)
    for name, better in _METRIC_PREFERENCE:
        if name in shared:
            return name, better
    for name in sorted(shared):
        return name, True
    return None


def _version_matches_run(version: dict, mlflow_run_id: str, model_uri: str) -> bool:
    """Tolerant match between a registry version and the training run that
    produced it (mirror schemas vary): direct run-id fields first, then the
    source/model_uri lineage string."""
    if not isinstance(version, dict):
        return False
    for key in ("mlflow_run_id", "run_id", "source_run_id"):
        if mlflow_run_id and version.get(key) == mlflow_run_id:
            return True
    src = str(version.get("source") or version.get("model_uri") or "")
    if mlflow_run_id and mlflow_run_id in src:
        return True
    return bool(model_uri and model_uri and src and src == model_uri)


def build_ml_engineer_graph(deps: GraphDeps):
    async def ground(state: dict) -> dict:
        tenant_id = state["tenant_id"]
        token = deps.obo_token or ""
        label = str(state.get("label_column") or state.get("target")
                    or state.get("target_column") or "").strip()
        if not label:
            return _fail(state, "No target column given — tell me which column to "
                                "predict (e.g. target_column=disposition).")

        # Resolve the dataset: explicit urn/id, else name search (inference idiom).
        dataset: dict = {}
        dataset_id = state.get("dataset_id")
        ref = str(state.get("dataset_urn") or state.get("dataset") or "").strip()
        if not dataset_id and ref.startswith("wr:"):
            dataset_id = ref.rsplit("/", 1)[-1]
        if deps.dataset_reader is None:
            return _fail(state, "Dataset catalog unavailable (no reader).")
        if not dataset_id:
            found = await deps.dataset_reader.list_datasets(
                tenant_id=tenant_id, auth_token=token, q=ref or None)
            exact = [d for d in found if d.get("name") == ref]
            dataset = (exact or found or [{}])[0]
            dataset_id = dataset.get("id")
        if not dataset_id:
            return _fail(state, f"No dataset found matching {ref!r}.")
        schema_info = await deps.dataset_reader.get_schema(
            tenant_id=tenant_id, dataset_id=dataset_id, auth_token=token)
        schema = schema_info.get("schema") or {}
        columns = list(schema.keys()) if isinstance(schema, dict) else []
        if not columns:
            return _fail(state, f"Dataset {dataset_id} has no readable schema — "
                                "cannot plan features.")
        if label not in columns:
            return _fail(state, f"Target column {label!r} is not in the dataset "
                                f"schema ({', '.join(columns[:12])}…).")
        row_count = schema_info.get("row_count")
        if isinstance(row_count, int) and row_count < 10:
            return _fail(state, f"Dataset has only {row_count} rows — too small "
                                "to train meaningful candidates.")

        catalog: list[dict] = []
        if deps.pipeline_reader is not None:
            catalog = await deps.pipeline_reader.list_algorithms(
                tenant_id=tenant_id, auth_token=token)
        runnable = [a.get("name") for a in catalog
                    if isinstance(a, dict) and a.get("name") and a.get("runnable", True)]
        picks = [n for n in _CANDIDATE_PREFERENCE if n in runnable]
        cap = int(state.get("candidates") or _MAX_CANDIDATES)
        picks = picks[:max(1, min(cap, _MAX_CANDIDATES))]
        if not picks:
            return _fail(state, "No runnable supervised algorithm available in the "
                                "pipeline catalog.")

        schemas: dict[str, dict] = {}
        for name in picks:
            detail = await deps.pipeline_reader.get_algorithm(
                tenant_id=tenant_id, algorithm=name, auth_token=token)
            schemas[name] = detail.get("parameters") or {}

        state.update({
            "dataset_id": dataset_id,
            "dataset_name": dataset.get("name") or dataset_id,
            "dataset_urn": dataset.get("urn")
                or f"wr:{tenant_id}:dataset:dataset/{dataset_id}",
            "dataset_version": schema_info.get("version_no"),
            "row_count": row_count, "columns": columns, "label_column": label,
            "candidate_algos": picks, "algo_schemas": schemas,
        })
        state.setdefault("trace", []).append(
            {"event": "tool_call_result", "tool_id": "dataset.inspect",
             "dataset": state["dataset_name"], "columns": len(columns),
             "candidates": picks})
        return state

    async def plan(state: dict) -> dict:
        if state.get("failed"):
            return state
        schemas = state["algo_schemas"]
        user = (
            f"Dataset: {state['dataset_name']} ({state.get('row_count')} rows)\n"
            f"Columns: {', '.join(state['columns'])}\n"
            f"Target column: {state['label_column']}\n"
            f"Candidate algorithm parameter schemas (JSON): "
            f"{json.dumps(schemas, default=str)[:2400]}\n"
            "Fill hyperparameters for each candidate now."
        )
        result = await deps.llm.chat(
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": user}],
            tenant_id=state["tenant_id"],
            response_format={"type": "json_object"},
            temperature=0.2, max_tokens=600)
        parsed = _extract_json(result.content)
        state["usage"] = {"input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens,
                          "model": result.model, "deployment": result.deployment}
        filled = parsed.get("candidates") if isinstance(parsed.get("candidates"), dict) else {}
        feats = parsed.get("feature_columns")
        if isinstance(feats, list):
            feats = [f for f in feats
                     if isinstance(f, str) and f in state["columns"]
                     and f != state["label_column"]] or None
        else:
            feats = None
        state["feature_columns"] = feats
        state["plans"] = {
            name: _coerce_hyperparameters(schemas.get(name) or {},
                                          filled.get(name) if isinstance(
                                              filled.get(name), dict) else {})
            for name in state["candidate_algos"]}
        rationale = parsed.get("rationale")
        state["plan_rationale"] = (rationale.strip()[:2000]
                                   if isinstance(rationale, str) and rationale.strip()
                                   else f"Schema-grounded defaults for "
                                        f"{len(state['plans'])} candidates.")
        state.setdefault("trace", []).append(
            {"event": "reflection", "iteration": 0, "model": result.model})
        return state

    async def train(state: dict) -> dict:
        if state.get("failed"):
            return state
        if deps.pipeline_writer is None or deps.replay:
            return _fail(state, "Training launches are disabled in this mode "
                                "(replay / no writer) — plan computed, nothing ran.")
        tenant_id = state["tenant_id"]
        token = deps.obo_token or ""
        # Pipeline template names are unique per tenant — suffix a short token so
        # repeated agent runs (and Temporal activity retries) don't 409-collide.
        run_tag = uuid.uuid4().hex[:6]
        candidates: list[dict] = []
        for algo in state["candidate_algos"]:
            params: dict[str, Any] = {**state["plans"].get(algo, {}),
                                      "label_column": state["label_column"]}
            if state.get("feature_columns"):
                params["feature_columns"] = state["feature_columns"]
            entry: dict[str, Any] = {"algorithm": algo, "params": params}
            try:
                run = await deps.pipeline_writer.instantiate(
                    tenant_id=tenant_id, algorithm=algo, auth_token=token,
                    dataset_refs={"TRAIN": state["dataset_urn"]}, params=params,
                    workspace_id=state.get("workspace_id"),
                    name=f"ml-engineer {algo} — {state['label_column']} [{run_tag}]")
                entry["run_id"] = run.get("id")
                entry["status"] = run.get("status") or "queued"
            except Exception as exc:  # noqa: BLE001 — reported honestly below
                entry["status"] = "launch_failed"
                entry["error"] = str(exc)[:300]
            candidates.append(entry)
            state.setdefault("trace", []).append(
                {"event": "tool_call_result", "tool_id": "pipeline.train",
                 "algorithm": algo, "run_id": entry.get("run_id"),
                 "status": entry["status"]})

        # Poll every launched run to a terminal state (bounded).
        for entry in candidates:
            run_id = entry.get("run_id")
            if not run_id:
                continue
            for _ in range(_POLL_MAX_TRIES):
                run = await deps.pipeline_reader.get_run(
                    tenant_id=tenant_id, run_id=run_id, auth_token=token)
                status = str(run.get("status") or "").lower()
                if status in _TERMINAL:
                    entry["status"] = status
                    entry["metrics"] = run.get("metrics") or {}
                    entry["mlflow_run_id"] = run.get("mlflow_run_id")
                    entry["model_uri"] = run.get("model_uri")
                    entry["error"] = run.get("error")
                    break
                await asyncio.sleep(_POLL_INTERVAL_S)
            else:
                entry["status"] = "timeout"
        state["candidates"] = candidates
        return state

    async def propose(state: dict) -> dict:
        if state.get("failed"):
            return state
        tenant_id = state["tenant_id"]
        token = deps.obo_token or ""
        candidates = state.get("candidates") or []
        succeeded = [c for c in candidates if c.get("status") == "succeeded"
                     and c.get("metrics")]
        if not succeeded:
            detail = "; ".join(f"{c['algorithm']}: {c.get('status')}"
                               f"{' — ' + str(c.get('error'))[:120] if c.get('error') else ''}"
                               for c in candidates) or "no candidates launched"
            return _fail(state, f"No candidate finished with metrics ({detail}). "
                                "Nothing worth proposing.")

        picked = _pick_primary_metric(succeeded)
        if picked is None:
            return _fail(state, "Candidates finished but report no comparable "
                                "metrics — refusing to rank blindly.")
        metric, higher_better = picked
        ranked = sorted(
            succeeded,
            key=lambda c: float(c["metrics"].get(metric, 0.0)),
            reverse=higher_better)
        winner = ranked[0]
        state["primary_metric"] = metric
        state["winner"] = winner

        # Resolve the winner's registered model version in the experiment registry.
        # The registry mirror is eventually-consistent, so poll a few times before
        # concluding the version is missing (closes the race with the async mirror).
        async def _resolve_once() -> tuple[str | None, int | None]:
            if deps.experiment_reader is None:
                return None, None
            models = await deps.experiment_reader.list_models(
                tenant_id=tenant_id, auth_token=token)
            for m in models:
                detail = await deps.experiment_reader.get_model(
                    tenant_id=tenant_id, model_id=str(m.get("id")), auth_token=token)
                for v in detail.get("versions") or []:
                    if _version_matches_run(v, winner.get("mlflow_run_id") or "",
                                            winner.get("model_uri") or ""):
                        state["model_name"] = m.get("name")
                        return str(m.get("id")), int(v.get("version") or v.get("version_no") or 0)
            return None, None

        model_id = version_no = None
        for _attempt in range(_RESOLVE_MAX_TRIES):
            model_id, version_no = await _resolve_once()
            if model_id and version_no:
                break
            if _attempt < _RESOLVE_MAX_TRIES - 1:
                await asyncio.sleep(_RESOLVE_INTERVAL_S)
        if not model_id or not version_no:
            return _fail(state, f"Winner ({winner['algorithm']}, "
                                f"{metric}={winner['metrics'].get(metric)}) trained, "
                                "but no matching registered model version was found "
                                "in the registry yet — re-run once the registry "
                                "mirror has caught up, or register it manually.")

        target_stage = str(state.get("target_stage") or "staging")
        lines = [f"ml-engineer evaluated {len(candidates)} candidate(s) on "
                 f"'{state['dataset_name']}' v{state.get('dataset_version')} "
                 f"({state.get('row_count')} rows) predicting "
                 f"'{state['label_column']}':"]
        for c in candidates:
            met = ", ".join(f"{k}={v}" for k, v in (c.get("metrics") or {}).items())
            lines.append(f"- {c['algorithm']}: {c.get('status')}"
                         f"{' [' + met + ']' if met else ''}")
        lines.append(f"Winner: {winner['algorithm']} by {metric}="
                     f"{winner['metrics'].get(metric)} → promote "
                     f"{state.get('model_name') or model_id} v{version_no} "
                     f"to {target_stage}. {state.get('plan_rationale', '')}")
        rationale = "\n".join(lines)[:4000]

        model_version_urn = (f"wr:{tenant_id}:experiment:model_version/"
                             f"{model_id}:{version_no}")
        state["write_intent"] = WriteIntent(
            tool_id=PROMOTE_TOOL_ID, tool_version=PROMOTE_TOOL_VERSION,
            tier="write-proposal", side_effects="reversible",
            args={"model_id": model_id, "version": version_no,
                  "model_version_urn": model_version_urn,
                  "target_stage": target_stage, "rationale": rationale},
            rationale=rationale,
            affected_urns=[model_version_urn],
            required_action="experiment.model.update",
            workspace_id=state.get("workspace_id"),
            predicted_effect={
                "summary": (f"Request promotion of {state.get('model_name') or model_id} "
                            f"v{version_no} to {target_stage} "
                            f"({metric}={winner['metrics'].get(metric)}); a second "
                            "human must still approve the promotion itself."),
                "reversibility": "reversible", "blast_radius": 1})
        state["report"] = rationale
        state.setdefault("trace", []).append(
            {"event": "proposal_created", "tool_id": PROMOTE_TOOL_ID,
             "model_id": model_id, "version": version_no})
        return state

    g = StateGraph(dict)
    g.add_node("ground", ground)
    g.add_node("plan", plan)
    g.add_node("train", train)
    g.add_node("propose", propose)
    g.set_entry_point("ground")
    g.add_edge("ground", "plan")
    g.add_edge("plan", "train")
    g.add_edge("train", "propose")
    g.add_edge("propose", END)
    return g.compile()


@register("ml_engineer.v1")
def ml_engineer_module():
    return build_ml_engineer_graph


async def run_ml_engineer(deps: GraphDeps, inputs: dict) -> GraphOutcome:
    graph = build_ml_engineer_graph(deps)
    final = await graph.ainvoke(dict(inputs))
    return GraphOutcome(
        final_text=final.get("report") or "ml-engineer produced no report.",
        write_intent=final.get("write_intent"),
        usage=final.get("usage", {}),
        trace=final.get("trace", []),
        structured={"failed": bool(final.get("failed")),
                    "dataset": final.get("dataset_name"),
                    "label_column": final.get("label_column"),
                    "primary_metric": final.get("primary_metric"),
                    "candidates": [
                        {"algorithm": c.get("algorithm"), "status": c.get("status"),
                         "metrics": c.get("metrics"), "run_id": c.get("run_id")}
                        for c in final.get("candidates") or []]},
        evidence=[])
