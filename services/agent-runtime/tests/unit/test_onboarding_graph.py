"""data-onboarding LangGraph proposes an ingestion config + column mapping as a
WRITE INTENT (never a direct write), grounded in the connector catalog + a source
preview + workspace/tenant memory (ART-FR-040)."""

from __future__ import annotations

from app.adapters.fakes import FakeIngestionReader, FakeLlm, FakeMemory
from app.graphs.base import GraphDeps
from app.graphs.onboarding import run_onboarding
from tests.conftest import TENANT_A

_PLAN = (
    '{"connector_type":"s3","ingestion_mode":"file_upload","file_format":"csv",'
    '"target_dataset_name":"claims_raw",'
    '"column_mapping":[{"source":"Claim ID","target":"claim_id","type":"string","nullable":false},'
    '{"source":"Amount","target":"amount","type":"number","nullable":true}],'
    '"rationale":"S3 CSV source; types grounded in the previewed columns."}'
)


async def test_onboarding_produces_ingestion_create_intent():
    ingestion = FakeIngestionReader(
        preview={"columns": ["Claim ID", "Amount"],
                 "rows": [["CLM-1", "1250.50"]]})
    deps = GraphDeps(
        llm=FakeLlm(content=_PLAN),
        memory=FakeMemory(results=[{"content": "prior claims onboard -> claims_raw"}]),
        ingestion_reader=ingestion, prompt_params={}, obo_token="tok")

    outcome = await run_onboarding(deps, {
        "tenant_id": TENANT_A,
        "query": "Onboard the claims CSV from S3 as a dataset",
        "connection_id": "conn-1", "source_path": "s3://bucket/claims/*.csv"})

    wi = outcome.write_intent
    assert wi is not None
    assert wi.tool_id == "ingestion.create"
    assert wi.tool_version == "1.0.0"
    assert wi.tier == "write-proposal"
    assert wi.side_effects == "reversible"
    assert wi.args["ingestion_mode"] == "file_upload"
    assert wi.args["file_format"] == "csv"
    assert wi.args["new_dataset"]["name"] == "claims_raw"
    assert wi.args["connection_id"] == "conn-1"
    assert len(wi.args["column_mapping"]) == 2
    assert wi.rationale  # a grounded rationale is attached
    assert any(u.endswith("dataset/claims_raw") for u in wi.affected_urns)
    # grounding calls actually happened
    ops = [c["op"] for c in ingestion.calls]
    assert "connector_types" in ops and "preview" in ops
    assert outcome.evidence  # retrieved memory surfaced as evidence
    assert outcome.usage["output_tokens"] > 0  # a real model was invoked


async def test_onboarding_defensive_on_bad_json():
    deps = GraphDeps(llm=FakeLlm(content="not json"), memory=FakeMemory(),
                     ingestion_reader=FakeIngestionReader(), prompt_params={},
                     obo_token="tok")
    outcome = await run_onboarding(deps, {
        "tenant_id": TENANT_A, "query": "onboard something"})
    # falls back to a valid proposal (object-store connector, file_upload/csv)
    wi = outcome.write_intent
    assert wi is not None
    assert wi.tool_id == "ingestion.create"
    assert wi.args["ingestion_mode"] == "file_upload"
    assert wi.args["connector_type"] in ("s3", "postgres")
