"""dashboard-designer agent (ART-FR-040) — governed Insights "build a dashboard + charts".

Proposal-mode LangGraph agent: it drafts a dashboard (a title + N charts) grounded
STRICTLY in the real semantic layer (published measures + dimensions from
semantic-service) and the real chart-type catalog (chart-service). It never writes:
it emits ONE WriteIntent for ``chart.dashboard.create`` that the runtime converts
into a Proposal requiring human approval; on approve the tool-plane federates to
chart-service's create path under a signed grant.

Real StateGraph: ground -> design -> propose.
* ground  — fetch governed metrics + dimensions (semantic-service) + chart types
            (chart-service) + prior-dashboard memory (workspace+tenant RAG).
* design  — the REAL model (ai-gateway -> Ollama) drafts a dashboard spec whose
            charts reference ONLY grounded measures/dimensions and grounded
            chart_types.
* propose — WriteIntent(chart.dashboard.create) with the drafted spec + rationale.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langgraph.graph import END, StateGraph

from app.adapters.memory import GroundingDegraded
from app.domain.urn import dashboard_urn
from app.graphs.base import GraphDeps, GraphOutcome, WriteIntent, register
from app.prompts import system_prompt

DASHBOARD_TOOL_ID = "chart.dashboard.create"
DASHBOARD_TOOL_VERSION = "1.0.0"

# The chart-type families the designer knows how to fill a config for. A grounded
# chart type outside these families still gets proposed, but with a measure/
# dimension-only config the resolver can map (BR: never invent a family).
_KNOWN_FAMILIES = ("axis", "grid", "single")

_SYS = system_prompt("dashboard_designer.system")


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return s[:64] or "dashboard"


def build_dashboard_designer_graph(deps: GraphDeps):
    async def ground(state: dict) -> dict:
        tenant_id = state["tenant_id"]
        workspace_id = state.get("workspace_id")
        token = deps.obo_token or ""
        metrics: list[dict] = []
        dimensions: list[dict] = []
        chart_types: list[dict] = []
        memories: list[dict] = []
        verified_queries: list[dict] = []

        if deps.semantic_reader is not None:
            try:
                metrics = await deps.semantic_reader.get_metrics(
                    tenant_id=tenant_id, auth_token=token, workspace_id=workspace_id)
                dimensions = await deps.semantic_reader.get_dimensions(
                    tenant_id=tenant_id, auth_token=token, workspace_id=workspace_id)
                # SEM-FR-041: approved verified NL->SQL pairs as proven query
                # conventions for the designer (best-effort, same credential).
                if workspace_id:
                    verified_queries = (
                        await deps.semantic_reader.search_verified_queries(
                            tenant_id=tenant_id, auth_token=token,
                            query=_ground_query(state), workspace_id=workspace_id,
                            top_k=5))
            except GroundingDegraded as exc:
                # semantic-service refused the credential (401/403): proceed
                # ungrounded but make the degradation VISIBLE (never silent).
                state["grounding_degraded"] = {"status": exc.status_code,
                                               "source": "semantic-service"}
                state.setdefault("trace", []).append(
                    {"event": "grounding_degraded", "source": "semantic-service",
                     "status": exc.status_code})

        if deps.catalog_reader is not None:
            chart_types = await deps.catalog_reader.list_chart_types(auth_token=token)

        if deps.memory is not None:
            try:
                # Prior-dashboard memory (workspace+tenant scopes per memory_policy)
                # so the designer can reuse conventions from earlier dashboards.
                memories = await deps.memory.retrieve(
                    tenant_id=tenant_id, query=_ground_query(state), auth_token=token,
                    top_k=5, snapshot_ver=deps.memory_snapshot_ver)
            except GroundingDegraded as exc:
                memories = []
                state.setdefault("trace", []).append(
                    {"event": "grounding_degraded", "source": "memory-service",
                     "status": exc.status_code})

        state["metrics"] = metrics
        state["dimensions"] = dimensions
        state["chart_types"] = chart_types
        state["memories"] = memories
        state["verified_queries"] = verified_queries
        state.setdefault("trace", []).append(
            {"event": "tool_call_result", "tool_id": "semantic.get_metrics",
             "metrics": len(metrics), "dimensions": len(dimensions),
             "verified_queries": len(verified_queries)})
        state.setdefault("trace", []).append(
            {"event": "tool_call_result", "tool_id": "chart.chart_types.list",
             "chart_types": len(chart_types), "memories": len(memories)})
        return state

    async def design(state: dict) -> dict:
        metrics = state.get("metrics", [])
        dimensions = state.get("dimensions", [])
        chart_types = state.get("chart_types", [])
        metric_names = [m.get("name") for m in metrics if m.get("name")]
        dim_names = [d.get("name") for d in dimensions if d.get("name")]
        type_names = _catalog_type_names(chart_types)

        prior = [m.get("content", m) for m in state.get("memories", [])]
        verified_queries = state.get("verified_queries", [])
        user = (
            f"Request: {state.get('query') or 'Design a claims overview dashboard'}\n"
            "Semantic layer (grouped by MODEL — a chart uses measures and "
            "dimensions from ONE model only):\n"
            f"{_fmt_catalog_by_model(metrics, dimensions)}\n"
            f"Available chart_type names: {json.dumps(type_names)}\n"
            f"Approved verified queries (proven NL->SQL conventions to reuse "
            f"where relevant):\n{_fmt_verified_queries(verified_queries)}\n"
            f"Prior dashboards (for convention reuse): "
            f"{json.dumps(prior, default=str)[:800]}\n"
            "Draft the dashboard now."
        )
        result = await deps.llm.chat(
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": user}],
            tenant_id=state["tenant_id"], response_format={"type": "json_object"},
            temperature=0.2, max_tokens=700)
        parsed = _extract_json(result.content)
        state["model_output"] = result.content
        state["usage"] = {"input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens,
                          "model": result.model,
                          "deployment": getattr(result, "deployment", None)}
        state["spec"] = _normalise_spec(parsed, state, metric_names, dim_names,
                                        type_names)
        state.setdefault("trace", []).append(
            {"event": "reflection", "iteration": 0, "model": result.model,
             "charts": len(state["spec"]["charts"])})
        return state

    async def propose(state: dict) -> dict:
        spec = state["spec"]
        tenant_id = state["tenant_id"]
        slug = _slug(spec["title"])
        args = {
            "workspace_id": state.get("workspace_id"),
            "name": spec["title"],
            "module": "insights",
            "description": spec.get("description")
            or "Draft dashboard proposed by the dashboard-designer agent.",
            "charts": spec["charts"],
        }
        state["write_intent"] = WriteIntent(
            tool_id=DASHBOARD_TOOL_ID, tool_version=DASHBOARD_TOOL_VERSION,
            tier="write-proposal", side_effects="reversible", args=args,
            rationale=spec["rationale"],
            affected_urns=[dashboard_urn(tenant_id, slug)],
            required_action="chart.dashboard.create",
            predicted_effect={
                "summary": (f"Create draft dashboard '{spec['title']}' with "
                            f"{len(spec['charts'])} chart(s) over the governed "
                            "semantic layer."),
                "reversibility": "reversible", "blast_radius": 1})
        state.setdefault("trace", []).append(
            {"event": "proposal_created", "tool_id": DASHBOARD_TOOL_ID})
        return state

    g = StateGraph(dict)
    g.add_node("ground", ground)
    g.add_node("design", design)
    g.add_node("propose", propose)
    g.set_entry_point("ground")
    g.add_edge("ground", "design")
    g.add_edge("design", "propose")
    g.add_edge("propose", END)
    return g.compile()


def _ground_query(state: dict) -> str:
    return f"dashboard design {state.get('query') or 'claims overview'}"


def _catalog_type_names(chart_types: list[dict]) -> list[str]:
    """Names of grounded chart types, preferring the families the designer can
    fill a config for; falls back to all grounded names."""
    preferred = [t.get("name") for t in chart_types
                 if t.get("name") and t.get("family") in _KNOWN_FAMILIES]
    if preferred:
        return preferred
    return [t.get("name") for t in chart_types if t.get("name")]


def _fmt_metrics(metrics: list[dict]) -> str:
    lines = [f"- {m.get('name')}: {m.get('agg') or '?'} — {m.get('description') or ''}"
             for m in metrics if m.get("name")]
    return "\n".join(lines) or "(none available)"


def _fmt_dims(dimensions: list[dict]) -> str:
    lines = [f"- {d.get('name')}: {d.get('type') or '?'}"
             for d in dimensions if d.get("name")]
    return "\n".join(lines) or "(none available)"


def _fmt_catalog_by_model(metrics: list[dict], dimensions: list[dict]) -> str:
    """Present the semantic layer GROUPED BY MODEL. The workspace can hold many
    models whose measure/dimension names overlap (e.g. ``claim_count`` exists in
    several claims models); a flat list lets the designer mix measures from two
    models into one chart, which can never compile (each chart resolves against a
    single model). Grouping makes the one-model-per-chart boundary explicit."""
    models: dict[str, dict[str, list[str]]] = {}
    for m in metrics:
        nm, mdl = m.get("name"), m.get("model")
        if nm and mdl:
            models.setdefault(mdl, {"measures": [], "dimensions": []})["measures"].append(
                f"{nm} ({m.get('agg') or '?'})")
    for d in dimensions:
        nm, mdl = d.get("name"), d.get("model")
        if nm and mdl:
            models.setdefault(mdl, {"measures": [], "dimensions": []})["dimensions"].append(
                f"{nm}:{d.get('type') or '?'}")
    if not models:
        return "(no governed models available)"
    blocks = []
    for mdl in sorted(models):
        cat = models[mdl]
        blocks.append(
            f"MODEL {mdl}\n"
            f"  measures: {', '.join(cat['measures']) or '(none)'}\n"
            f"  dimensions: {', '.join(cat['dimensions']) or '(none)'}")
    return "\n".join(blocks)


def _model_index(items: list[dict]) -> dict[str, set]:
    """name-set per model from grounded metrics or dimensions."""
    idx: dict[str, set] = {}
    for it in items:
        nm, mdl = it.get("name"), it.get("model")
        if nm and mdl:
            idx.setdefault(mdl, set()).add(nm)
    return idx


def _fmt_verified_queries(verified_queries: list[dict]) -> str:
    lines = []
    for vq in verified_queries:
        nl = (vq.get("nl_text") or "").strip()
        sql = " ".join((vq.get("sql_text") or "").split())
        if not nl:
            continue
        lines.append(f"- Q: {nl}\n  SQL: {sql[:400]}")
    return "\n".join(lines) or "(none available)"


def _normalise_spec(parsed: dict, state: dict, metric_names: list[str],
                    dim_names: list[str], type_names: list[str]) -> dict:
    title = str(parsed.get("title") or "Claims Overview").strip()[:120]
    rationale = str(parsed.get("rationale")
                    or "Draft dashboard grounded in the governed semantic layer.")
    tset = set(type_names)
    default_type = type_names[0] if type_names else "grid_chart"

    # A chart resolves against exactly ONE semantic model at render time — every
    # measure and dimension it carries must belong to that model, or the compile
    # fails (UNKNOWN_METRIC / "workspace_id required"). Names collide across
    # models (``claim_count`` lives in several claims models), so we can't pick a
    # model by looking up a single measure name. Instead, for each chart we score
    # every grounded model by how many of the chart's proposed refs it actually
    # contains, pick the best-fit model, and keep only that model's refs.
    measures_by_model = _model_index(state.get("metrics", []))
    dims_by_model = _model_index(state.get("dimensions", []))
    all_models = set(measures_by_model) | set(dims_by_model)

    def _resolve(raw_measures: list[str], raw_dims: list[str],
                 declared: str | None) -> tuple[str, list[str], list[str]]:
        rm, rd = set(raw_measures), set(raw_dims)
        # Declared model gets first look on ties (strict >), so a valid explicit
        # model wins when scores are equal.
        order = ([declared] if declared in all_models else []) + sorted(all_models)
        best_mdl, best_score = "", 0
        for mdl in dict.fromkeys(order):
            # Measures drive the compile, so weight them above dimensions.
            score = len(rm & measures_by_model.get(mdl, set())) * 2 + \
                len(rd & dims_by_model.get(mdl, set()))
            if score > best_score:
                best_score, best_mdl = score, mdl
        if best_score == 0:
            return "", [], []
        measures = [m for m in raw_measures if m in measures_by_model.get(best_mdl, set())]
        dims = [d for d in raw_dims if d in dims_by_model.get(best_mdl, set())]
        return best_mdl, measures, dims

    charts: list[dict] = []
    for raw in (parsed.get("charts") or []):
        if not isinstance(raw, dict):
            continue
        model, measures, dims = _resolve(
            _as_list(raw.get("measures")), _as_list(raw.get("dimensions")),
            raw.get("model"))
        # Drop a chart that isn't a single-model-coherent, plottable unit: it must
        # resolve to one model AND keep at least one real measure. A chart with no
        # measure has nothing to resolve at render time ("chart has no measures to
        # resolve"), so a dimension-only chart is dropped rather than emitted broken.
        if not model or not measures:
            continue
        ctype = raw.get("chart_type")
        if ctype not in tset:
            ctype = default_type
        charts.append({
            "name": str(raw.get("name") or "Chart").strip()[:120],
            "chart_type": ctype,
            "model": model,
            "measures": measures,
            "dimensions": dims,
            "filters": _as_list(raw.get("filters")),
        })

    # Deterministic grounded fallback: if the model produced nothing usable but we
    # DID ground real refs, propose a single grid chart from the richest model so
    # the proposal still references real, model-coherent semantic refs.
    if not charts and all_models:
        best = max(all_models, key=lambda m: len(measures_by_model.get(m, set())))
        ms = sorted(measures_by_model.get(best, set()))[:3]
        ds = sorted(dims_by_model.get(best, set()))[:1]
        if ms:
            charts.append({
                "name": "Overview", "chart_type": default_type, "model": best,
                "measures": ms, "dimensions": ds, "filters": []})

    return {"title": title, "rationale": rationale[:4000], "charts": charts[:6]}


def _as_list(v: Any) -> list:
    if isinstance(v, list):
        return [x for x in v if x is not None]
    if v in (None, ""):
        return []
    return [v]


@register("dashboard_designer.v1")
def dashboard_designer_module():
    return build_dashboard_designer_graph


async def run_dashboard_designer(deps: GraphDeps, inputs: dict) -> GraphOutcome:
    graph = build_dashboard_designer_graph(deps)
    final = await graph.ainvoke(dict(inputs))
    spec = final.get("spec", {})
    charts = spec.get("charts", [])
    return GraphOutcome(
        final_text=(f"Proposed dashboard '{spec.get('title', 'Dashboard')}' with "
                    f"{len(charts)} chart(s) grounded in the semantic layer."),
        write_intent=final.get("write_intent"),
        usage=final.get("usage", {}),
        trace=final.get("trace", []),
        structured=spec,
        evidence=final.get("memories", []))
