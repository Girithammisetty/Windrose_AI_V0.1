"""BRD 62 — the data-pipeline-builder agent produces a GOVERNED data-prep pipeline
create WRITE INTENT (never a direct write), grounded in the live operator catalog,
and wires the chosen operators into a validated linear DAG.
"""

from __future__ import annotations

from app.adapters.fakes import FakeLlm, FakeMemory, FakePipelineReader
from app.graphs.base import GraphDeps
from app.graphs.data_pipeline_builder import CREATE_TOOL_ID, run_data_pipeline_builder
from tests.conftest import TENANT_A

_PLAN_JSON = (
    '{"name": "Claims cleanup",'
    ' "operators": ['
    '   {"component": "filter-data", "parameters": {"expression": "amount > 0"}},'
    '   {"component": "handle-missing-values", "parameters": {"strategy": "mean"}},'
    '   {"component": "bogus-operator", "parameters": {}},'
    '   {"component": "one-hot-encoder", "parameters": {"columns": ["merchant"]}}'
    ' ],'
    ' "rationale": "Drop non-positive amounts, impute nulls, one-hot the merchant."}'
)


def _deps(**over):
    base = dict(
        llm=FakeLlm(content=_PLAN_JSON),
        memory=FakeMemory(results=[{"content": "prior claims prep dropped negatives"}]),
        pipeline_reader=FakePipelineReader(),
        prompt_params={}, obo_token="tok")
    base.update(over)
    return GraphDeps(**base)


async def test_builds_governed_data_prep_pipeline_intent():
    deps = _deps()
    outcome = await run_data_pipeline_builder(deps, {
        "tenant_id": TENANT_A, "query": "clean up the claims dataset: drop non-positive "
        "amounts, fill missing values, one-hot the merchant column",
        "dataset": "claims", "workspace_id": "ws-1"})

    wi = outcome.write_intent
    assert wi is not None
    assert wi.tool_id == CREATE_TOOL_ID == "pipeline.template.create"
    assert wi.tier == "write-proposal"
    assert wi.args["pipeline_type"] == "data_prep"
    assert wi.args["workspace_id"] == "ws-1"
    assert wi.required_action == "pipeline.template.create"

    definition = wi.args["definition"]
    comps = [n["component"] for n in definition["nodes"]]
    # A validated LINEAR DAG: read → chosen operators → write.
    assert comps[0] == "read-from-warehouse"
    assert comps[-1] == "write-to-warehouse"
    # The unknown "bogus-operator" was dropped (fail safe); the 3 valid ops remain.
    assert "filter-data" in comps and "handle-missing-values" in comps
    assert "one-hot-encoder" in comps and "bogus-operator" not in comps
    # Edges chain the nodes end to end (n nodes → n-1 edges).
    assert len(definition["edges"]) == len(definition["nodes"]) - 1
    # The read node points at the resolved dataset URN.
    read = next(n for n in definition["nodes"] if n["component"] == "read-from-warehouse")
    assert read["parameters"]["dataset"].startswith(f"wr:{TENANT_A}:dataset:")


async def test_grounds_on_operator_catalog_and_memory():
    deps = _deps()
    await run_data_pipeline_builder(deps, {
        "tenant_id": TENANT_A, "query": "dedupe rows", "dataset": "claims"})
    assert any(c["op"] == "list_components" for c in deps.pipeline_reader.calls)
    assert deps.memory.calls  # RAG grounding attempted


async def test_defensive_on_bad_json():
    deps = _deps(llm=FakeLlm(content="not json"))
    outcome = await run_data_pipeline_builder(deps, {
        "tenant_id": TENANT_A, "query": "prep the data", "dataset": "claims"})
    # Still a valid proposal: read → (no operators) → write, never a crash.
    wi = outcome.write_intent
    comps = [n["component"] for n in wi.args["definition"]["nodes"]]
    assert comps == ["read-from-warehouse", "write-to-warehouse"]
