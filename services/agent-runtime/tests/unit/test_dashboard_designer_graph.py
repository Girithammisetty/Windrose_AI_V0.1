"""dashboard-designer LangGraph drafts a dashboard grounded in the REAL semantic
layer + chart-type catalog and emits ONE chart.dashboard.create WRITE INTENT
(never a direct write) — ART-FR-040."""

from __future__ import annotations

from app.adapters.fakes import (
    FakeChartCatalog,
    FakeLlm,
    FakeMemory,
    FakeSemanticReader,
)
from app.graphs.base import GraphDeps
from app.graphs.dashboard_designer import run_dashboard_designer
from tests.conftest import TENANT_A

WS = "ws-claims"

_GOOD = (
    '{"title":"Claims Overview","rationale":"Grounded claims KPIs by type and time.",'
    '"charts":[{"name":"Claims by type","chart_type":"vertical_bar_chart",'
    '"measures":["claim_count"],"dimensions":["claim_type"],"filters":[]},'
    '{"name":"Amount over time","chart_type":"line_chart",'
    '"measures":["total_amount"],"dimensions":["created_at"],"filters":[]}]}'
)


def _deps(llm, semantic=None, catalog=None, memory=None) -> GraphDeps:
    return GraphDeps(
        llm=llm,
        semantic_reader=semantic or FakeSemanticReader(),
        catalog_reader=catalog or FakeChartCatalog(),
        memory=memory or FakeMemory(results=[{"content": "prior: Claims Insights dashboard"}]),
        obo_token="tok")


async def test_produces_dashboard_write_intent_grounded_in_real_refs():
    semantic = FakeSemanticReader()
    catalog = FakeChartCatalog()
    deps = _deps(FakeLlm(content=_GOOD), semantic=semantic, catalog=catalog)
    outcome = await run_dashboard_designer(
        deps, {"tenant_id": TENANT_A, "workspace_id": WS,
               "query": "Design a claims overview dashboard"})

    wi = outcome.write_intent
    assert wi is not None
    assert wi.tool_id == "chart.dashboard.create"
    assert wi.tool_version == "1.0.0"
    assert wi.tier == "write-proposal"
    assert wi.side_effects == "reversible"
    assert wi.affected_urns == [f"wr:{TENANT_A}:chart:dashboard/claims-overview"]

    charts = wi.args["charts"]
    assert len(charts) >= 1
    # every chart references ONLY grounded semantic refs + a grounded chart type
    valid_measures = {"claim_count", "total_amount"}
    valid_dims = {"claim_type", "created_at"}
    valid_types = {"vertical_bar_chart", "line_chart", "grid_chart", "big_number"}
    for ch in charts:
        assert ch["chart_type"] in valid_types
        assert set(ch["measures"]) <= valid_measures
        assert set(ch["dimensions"]) <= valid_dims
        assert ch["measures"] or ch["dimensions"]
    # at least one chart references a real measure
    assert any(ch["measures"] for ch in charts)

    # grounding actually happened (semantic + chart-type reads)
    assert any(c["op"] == "get_metrics" for c in semantic.calls)
    assert any(c["op"] == "get_dimensions" for c in semantic.calls)
    # SEM-FR-041: verified NL->SQL pairs are retrieved as grounding too
    assert any(c["op"] == "search_verified_queries" for c in semantic.calls)
    assert catalog.calls and catalog.calls[0]["op"] == "list_chart_types"
    assert outcome.usage["output_tokens"] > 0  # a model was invoked
    assert outcome.evidence  # prior-dashboard memory surfaced as evidence


async def test_verified_queries_ground_the_design_prompt():
    # the approved verified NL->SQL pair must reach the model's prompt so it is
    # GENUINELY consumed (grounding, not a dead read).
    semantic = FakeSemanticReader(verified_queries=[
        {"id": "vq-9", "nl_text": "Average settlement time by adjuster",
         "sql_text": "SELECT adjuster, AVG(days_to_settle) FROM claims GROUP BY 1",
         "variables": [], "tags": ["ops"], "model_id": "m-1", "score": 0.88}])
    llm = FakeLlm(content=_GOOD)
    deps = _deps(llm, semantic=semantic)
    await run_dashboard_designer(
        deps, {"tenant_id": TENANT_A, "workspace_id": WS,
               "query": "settlement performance"})

    assert any(c["op"] == "search_verified_queries" and c["workspace_id"] == WS
               for c in semantic.calls)
    # the pair's NL question surfaced in the design prompt the model received
    prompts = [msg["content"] for call in llm.calls for msg in call["messages"]
               if msg["role"] == "user"]
    assert any("Average settlement time by adjuster" in p for p in prompts)


async def test_verified_query_search_skipped_without_workspace():
    semantic = FakeSemanticReader()
    deps = _deps(FakeLlm(content=_GOOD), semantic=semantic)
    await run_dashboard_designer(deps, {"tenant_id": TENANT_A})
    # workspace-scoped only (BR-14): no workspace -> no verified-query search
    assert not any(c["op"] == "search_verified_queries" for c in semantic.calls)


async def test_drops_hallucinated_refs_and_still_proposes():
    # model invents a metric + a chart type that are NOT grounded
    bad = ('{"title":"Bad","rationale":"x","charts":[{"name":"c1",'
           '"chart_type":"pie_of_lies","measures":["revenue_made_up"],'
           '"dimensions":["region_made_up"],"filters":[]}]}')
    deps = _deps(FakeLlm(content=bad))
    outcome = await run_dashboard_designer(
        deps, {"tenant_id": TENANT_A, "workspace_id": WS})
    wi = outcome.write_intent
    assert wi is not None
    charts = wi.args["charts"]
    # the hallucinated chart is dropped; a deterministic grounded fallback remains
    assert len(charts) >= 1
    valid_measures = {"claim_count", "total_amount"}
    for ch in charts:
        assert set(ch["measures"]) <= valid_measures
        assert ch["chart_type"] in {"vertical_bar_chart", "line_chart",
                                    "grid_chart", "big_number"}


async def test_defensive_on_bad_json():
    deps = _deps(FakeLlm(content="not json at all"))
    outcome = await run_dashboard_designer(
        deps, {"tenant_id": TENANT_A, "workspace_id": WS})
    # falls back to a valid grounded proposal rather than crashing
    assert outcome.write_intent is not None
    assert outcome.write_intent.args["charts"]
