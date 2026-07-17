"""data-onboarding agent (ART-FR-040) — the governed
"create a connection -> configure ingestion -> map columns".

Proposal-mode LangGraph. Grounds on ingestion-service (the connector-type catalog
+ a saved connection's previewed source schema) and workspace/tenant memory of
prior onboarding configs, then PROPOSES an ingestion config + column mapping as a
WRITE INTENT for the ``ingestion.create`` tool — never a direct write. The runtime
converts the intent into a Proposal requiring human approval; on approve it
executes via tool-plane under a signed grant, creating a new dataset (a reversible
side effect).

Real LangGraph StateGraph: ground -> plan -> propose.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langgraph.graph import END, StateGraph

from app.adapters.memory import GroundingDegraded
from app.graphs.base import GraphDeps, GraphOutcome, WriteIntent, register

ONBOARDING_TOOL_ID = "ingestion.create"
ONBOARDING_TOOL_VERSION = "1.0.0"

INGESTION_MODES = ("file_upload", "query", "scheduled_run", "webhook_batch")
FILE_FORMATS = ("csv", "tsv", "json", "jsonl", "parquet", "avro")

_SYS = (
    "You are Windrose's data-onboarding agent. Given a user's request to onboard a "
    "data source, the catalog of available connector types, and (when available) a "
    "preview of the source's columns, draft an ingestion config and a column "
    "mapping. Respond with ONLY a JSON object: "
    '{"connector_type": one connector_type id from the catalog, '
    '"ingestion_mode": one of ["file_upload","query","scheduled_run","webhook_batch"], '
    '"file_format": one of ["csv","tsv","json","jsonl","parquet","avro"] or null, '
    '"target_dataset_name": short_snake_case dataset name, '
    '"column_mapping": [{"source": string, "target": snake_case string, '
    '"type": one of ["string","integer","number","boolean","timestamp","date"], '
    '"nullable": boolean}], '
    '"rationale": one concise sentence citing the grounding evidence}. '
    "Ground the column types/nullability in the previewed schema when present. "
    "No prose outside JSON."
)


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(text).lower()).strip("_")


def build_onboarding_graph(deps: GraphDeps):
    async def ground(state: dict) -> dict:
        auth = deps.obo_token or ""
        connector_types: list[dict] = []
        preview: dict = {}
        memories: list[dict] = []
        if deps.ingestion_reader is not None:
            connector_types = await deps.ingestion_reader.connector_types(
                tenant_id=state["tenant_id"], auth_token=auth)
            # A source preview needs a SAVED connection + a target (table/path/query).
            # Best-effort: skipped for a from-scratch onboard (no connection yet).
            conn_id = state.get("connection_id")
            if conn_id:
                preview = await deps.ingestion_reader.preview(
                    tenant_id=state["tenant_id"], connection_id=conn_id, auth_token=auth,
                    table=state.get("source_table"), path=state.get("source_path"),
                    query=state.get("source_query"))
        if deps.memory is not None:
            try:
                # Workspace+tenant onboarding memory grounds the config (prior
                # mappings/dataset conventions). Replay pins to a corpus snapshot.
                memories = await deps.memory.retrieve(
                    tenant_id=state["tenant_id"],
                    query=f"onboard data source {state.get('query', '')}"[:400],
                    auth_token=auth, top_k=5, snapshot_ver=deps.memory_snapshot_ver)
            except GroundingDegraded as exc:
                memories = []
                state["grounding_degraded"] = {"status": exc.status_code,
                                               "source": "memory-service"}
                state.setdefault("trace", []).append(
                    {"event": "grounding_degraded", "source": "memory-service",
                     "status": exc.status_code})
        state["connector_types"] = connector_types
        state["preview"] = preview
        state["memories"] = memories
        state.setdefault("trace", []).append(
            {"event": "tool_call_result", "tool_id": "ingestion.connector_types.list",
             "connector_types": len(connector_types),
             "preview_columns": len((preview or {}).get("columns") or []),
             "memories": len(memories)})
        return state

    async def plan(state: dict) -> dict:
        catalog = [{"connector_type": t.get("connector_type"),
                    "display_name": t.get("display_name"),
                    "category": t.get("category")}
                   for t in state.get("connector_types", [])]
        preview = state.get("preview") or {}
        mems = [m.get("content", m) for m in state.get("memories", [])]
        user = (
            f"Onboarding request: {state.get('query', '')}\n"
            f"Available connector types: {json.dumps(catalog, default=str)[:1500]}\n"
            f"Source preview (columns/rows): {json.dumps(preview, default=str)[:1500]}\n"
            f"Prior onboarding memory: {json.dumps(mems, default=str)[:1000]}\n"
            "Draft the ingestion config + column mapping now."
        )
        result = await deps.llm.chat(
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": user}],
            tenant_id=state["tenant_id"],
            response_format={"type": "json_object"},
            temperature=0.2, max_tokens=600)
        state["model_output"] = result.content
        state["usage"] = {"input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens,
                          "model": result.model, "deployment": result.deployment}
        state["config"] = _normalise(_extract_json(result.content), state)
        state.setdefault("trace", []).append(
            {"event": "reflection", "iteration": 0, "model": result.model})
        return state

    async def propose(state: dict) -> dict:
        cfg = state["config"]
        tenant = state["tenant_id"]
        workspace = state.get("workspace_id") or "00000000-0000-0000-0000-000000000000"
        args: dict[str, Any] = {
            "ingestion_mode": cfg["ingestion_mode"],
            "file_format": cfg["file_format"],
            "new_dataset": {"name": cfg["target_dataset_name"],
                            "description": f"Onboarded via data-onboarding agent: "
                                           f"{cfg['rationale']}"[:500]},
            "connector_type": cfg["connector_type"],
            "column_mapping": cfg["column_mapping"],
            "workspace_id": workspace,
        }
        if state.get("connection_id"):
            args["connection_id"] = state["connection_id"]
        affected = [f"wr:{tenant}:dataset:dataset/{_slug(cfg['target_dataset_name'])}"]
        if state.get("connection_id"):
            affected.append(f"wr:{tenant}:ingestion:connection/{state['connection_id']}")
        state["write_intent"] = WriteIntent(
            tool_id=ONBOARDING_TOOL_ID, tool_version=ONBOARDING_TOOL_VERSION,
            tier="write-proposal", side_effects="reversible", args=args,
            rationale=cfg["rationale"],
            affected_urns=affected,
            required_action="ingestion.ingestion.create",
            predicted_effect={
                "summary": (f"Create a {cfg['connector_type']}/{cfg['ingestion_mode']} "
                            f"ingestion into new dataset '{cfg['target_dataset_name']}' "
                            f"with {len(cfg['column_mapping'])} mapped columns."),
                "reversibility": "reversible", "blast_radius": 1})
        state.setdefault("trace", []).append(
            {"event": "proposal_created", "tool_id": ONBOARDING_TOOL_ID})
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


def _connector_ids(state: dict) -> list[str]:
    return [t.get("connector_type") for t in state.get("connector_types", [])
            if t.get("connector_type")]


def _normalise(parsed: dict, state: dict) -> dict:
    known = _connector_ids(state)
    ctype = str(parsed.get("connector_type", "")).lower()
    if known and ctype not in known:
        # Fall back to the first object-store connector (CSV-from-S3 style), else
        # the first available type — never invent a type outside the catalog.
        obj = next((t.get("connector_type") for t in state.get("connector_types", [])
                    if t.get("category") == "object-store"), None)
        ctype = obj or known[0]
    elif not known and not ctype:
        ctype = "s3"

    mode = str(parsed.get("ingestion_mode", "")).lower()
    if mode not in INGESTION_MODES:
        mode = "file_upload"

    fmt = parsed.get("file_format")
    fmt = fmt.lower() if isinstance(fmt, str) and fmt.lower() in FILE_FORMATS else None
    if mode == "file_upload" and fmt is None:
        fmt = "csv"

    name = _slug(parsed.get("target_dataset_name") or "onboarded_dataset") or "onboarded_dataset"

    mapping = []
    for col in parsed.get("column_mapping") or []:
        if not isinstance(col, dict):
            continue
        src = str(col.get("source", "")).strip()
        if not src:
            continue
        mapping.append({
            "source": src[:128],
            "target": _slug(col.get("target") or src)[:128] or _slug(src),
            "type": (str(col.get("type", "string")).lower()
                     if str(col.get("type", "string")).lower() in
                     ("string", "integer", "number", "boolean", "timestamp", "date")
                     else "string"),
            "nullable": bool(col.get("nullable", True))})

    rationale = str(parsed.get("rationale")
                    or "Drafted ingestion config grounded in the connector catalog "
                       "and source preview.")[:4000]
    return {"connector_type": ctype, "ingestion_mode": mode, "file_format": fmt,
            "target_dataset_name": name[:255], "column_mapping": mapping,
            "rationale": rationale}


@register("onboarding.v1")
def onboarding_module():
    return build_onboarding_graph


async def run_onboarding(deps: GraphDeps, inputs: dict) -> GraphOutcome:
    graph = build_onboarding_graph(deps)
    final = await graph.ainvoke(dict(inputs))
    cfg = final.get("config", {})
    return GraphOutcome(
        final_text=(f"Proposed onboarding: {cfg.get('connector_type', '?')} "
                    f"-> dataset '{cfg.get('target_dataset_name', '?')}' "
                    f"({len(cfg.get('column_mapping', []))} columns mapped)."),
        write_intent=final.get("write_intent"),
        usage=final.get("usage", {}),
        trace=final.get("trace", []),
        structured=cfg,
        evidence=final.get("memories", []))
