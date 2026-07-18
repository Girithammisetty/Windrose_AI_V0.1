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
from app.prompts import system_prompt

PROMOTE_TOOL_ID = "experiment.model.promote"
PROMOTE_TOOL_VERSION = "1.0.0"
# BRD 52 inc2 (Phase 2): the agent may INITIATE an ingestion from an EXISTING,
# admin-created connection — never create a connection, never see a credential.
INGEST_TOOL_ID = "ingestion.create"
INGEST_TOOL_VERSION = "1.0.0"

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

_SYS = system_prompt("ml_engineer.system")


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _hints_from_query(query: str) -> dict:
    """Extract structured ML hints (dataset, target column, target stage) from the
    free-text copilot request so the agent runs from natural language — not only
    from metadata.inputs. Matches `key=value` / `key: value` (the exact form the
    agent's own prompt suggests, e.g. ``target_column=disposition``). Explicit
    metadata.inputs still take precedence (these only fill what's missing)."""
    q = query or ""
    out: dict[str, str] = {}
    m = re.search(r"(?:target_column|label_column|label|target)\s*[=:]\s*([A-Za-z0-9_]+)", q, re.I)
    if m:
        out["label_column"] = m.group(1)
    m = re.search(r"dataset(?:_name|_urn)?\s*[=:]\s*([A-Za-z0-9_./:-]+)", q, re.I)
    if m:
        out["dataset"] = m.group(1)
    m = re.search(r"(?:target_stage|stage)\s*[=:]\s*([A-Za-z0-9_]+)", q, re.I)
    if m:
        out["target_stage"] = m.group(1)
    return out


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
        # Fill missing hints from the free-text request (the copilot passes the
        # user message as `query`); explicit metadata.inputs win via setdefault.
        for _k, _v in _hints_from_query(str(state.get("query") or "")).items():
            state.setdefault(_k, _v)
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
        # workspace_id MUST ride in the tool args, not just the intent: on
        # approval, experiment-service's MCP facade re-checks the deciding human's
        # experiment.model.update capability, and that grant is WORKSPACE-SCOPED
        # (perm projection key ...:experiment.model.update:{workspace_id}). The
        # model version lives in this workspace, so omitting it makes the facade
        # check at workspace=None and miss the ws-scoped grant -> 403. The run's
        # workspace is the model's workspace (the agent trains+registers here).
        workspace_id = state.get("workspace_id")
        promote_args = {"model_id": model_id, "version": version_no,
                        "model_version_urn": model_version_urn,
                        "target_stage": target_stage, "rationale": rationale}
        if workspace_id:
            promote_args["workspace_id"] = workspace_id
        state["write_intent"] = WriteIntent(
            tool_id=PROMOTE_TOOL_ID, tool_version=PROMOTE_TOOL_VERSION,
            tier="write-proposal", side_effects="reversible",
            args=promote_args,
            rationale=rationale,
            affected_urns=[model_version_urn],
            required_action="experiment.model.update",
            workspace_id=workspace_id,
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

    async def ingest(state: dict) -> dict:
        """BRD 52 inc2 (Phase 2): agent-initiated ingestion from an EXISTING
        connection. The agent lists the tenant's admin-created connections and may
        ONLY ingest from one of them — it can never provision a connection or
        handle a credential. An unknown/absent connection fails CLOSED (no
        proposal, logged refusal). A valid one becomes an ``ingestion.create``
        four-eyes proposal (same tiering as promote)."""
        req = state.get("refresh_from_connection") or {}
        conn_id = str(req.get("connection_id") or "").strip()
        tenant = state["tenant_id"]

        connections: list[dict] = []
        if deps.ingestion_reader is not None and hasattr(deps.ingestion_reader, "list_connections"):
            connections = await deps.ingestion_reader.list_connections(
                tenant_id=tenant, auth_token=deps.obo_token or "")
        known = {str(c.get("id")): c for c in connections if c.get("id")}
        state.setdefault("trace", []).append(
            {"event": "tool_call_result", "tool_id": "ingestion.connections.list",
             "count": len(known)})

        if not conn_id or conn_id not in known:
            state["failed"] = True
            state["report"] = (
                f"Ingestion refused: connection {conn_id or '(none)'} is not an existing, "
                "admin-created connection for this tenant. The agent may only ingest from "
                "connections an administrator has already provisioned.")
            state.setdefault("trace", []).append(
                {"event": "ingest_refused", "connection_id": conn_id,
                 "reason": "connection_not_found"})
            return state

        conn = known[conn_id]
        target = str(req.get("target_dataset_name")
                     or f"agent-refresh-{conn.get('name') or conn_id}")[:120]
        workspace = state.get("workspace_id") or "00000000-0000-0000-0000-000000000000"
        args: dict[str, Any] = {
            "ingestion_mode": str(req.get("ingestion_mode") or "full_refresh"),
            "connection_id": conn_id,
            "connector_type": conn.get("connector_type"),
            "new_dataset": {"name": target,
                            "description": "Refreshed by the ML-engineer agent (BRD 52 Phase 2)."},
            "workspace_id": workspace,
        }
        # Source selector travels through (facade records extras on the proposal;
        # only the create contract fields are applied at execution).
        for k in ("table", "path", "file_format"):
            if req.get(k):
                args[k] = req[k]
        if req.get("query"):
            args["statement"] = req["query"]

        state["write_intent"] = WriteIntent(
            tool_id=INGEST_TOOL_ID, tool_version=INGEST_TOOL_VERSION,
            tier="write-proposal", side_effects="reversible", args=args,
            rationale=(f"Refresh training data from existing connection "
                       f"'{conn.get('name') or conn_id}' into dataset '{target}'."),
            affected_urns=[f"wr:{tenant}:dataset:dataset/{target}",
                           f"wr:{tenant}:ingestion:connection/{conn_id}"],
            workspace_id=workspace,
            required_action="ingestion.ingestion.create",
            predicted_effect={
                "summary": (f"Ingest from the {conn.get('connector_type')} connection "
                            f"'{conn.get('name') or conn_id}' into a new dataset '{target}'."),
                "reversibility": "reversible", "blast_radius": 1})
        state["report"] = (f"Proposed an ingestion from connection "
                           f"'{conn.get('name') or conn_id}' into dataset '{target}' for approval.")
        state.setdefault("trace", []).append(
            {"event": "proposal_created", "tool_id": INGEST_TOOL_ID, "connection_id": conn_id})
        return state

    def _route_entry(state: dict) -> str:
        # A refresh directive routes to the ingestion path; otherwise the normal
        # train -> evaluate -> propose-promote loop.
        return "ingest" if (state.get("refresh_from_connection") or {}).get("connection_id") else "ground"  # noqa: E501

    g = StateGraph(dict)
    g.add_node("ground", ground)
    g.add_node("plan", plan)
    g.add_node("train", train)
    g.add_node("propose", propose)
    g.add_node("ingest", ingest)
    g.set_conditional_entry_point(_route_entry, {"ingest": "ingest", "ground": "ground"})
    g.add_edge("ground", "plan")
    g.add_edge("plan", "train")
    g.add_edge("train", "propose")
    g.add_edge("propose", END)
    g.add_edge("ingest", END)
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
