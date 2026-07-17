"""batch-inference LangGraph proposes an inference job as a WRITE INTENT (never a
direct write), grounded in the registered model's production version + the input
dataset schema + workspace/tenant memory, and gated on a deterministic
dataset<->model feature-compatibility check (ART-FR-040)."""

from __future__ import annotations

from app.adapters.fakes import FakeDatasetReader, FakeExperimentReader, FakeLlm, FakeMemory
from app.graphs.base import GraphDeps
from app.graphs.inference_agent import run_inference
from tests.conftest import TENANT_A

_QUERY = "Run batch inference with the production claims model on the latest claims dataset"


async def test_inference_produces_submit_intent_when_compatible():
    experiment = FakeExperimentReader()  # production v2 wants {amount: double}
    dataset = FakeDatasetReader()        # schema {amount: double, non-nullable}
    deps = GraphDeps(
        llm=FakeLlm(content="Dataset matches the model's feature contract; proceed."),
        memory=FakeMemory(results=[{"content": "prior claims scoring job -> scores v1"}]),
        experiment_reader=experiment, dataset_reader=dataset,
        prompt_params={}, obo_token="tok")

    outcome = await run_inference(deps, {"tenant_id": TENANT_A, "query": _QUERY})

    wi = outcome.write_intent
    assert wi is not None
    assert wi.tool_id == "inference.submit"
    assert wi.tool_version == "1.0.0"
    assert wi.tier == "write-proposal"
    assert wi.side_effects == "reversible"
    # a valid, executable job spec
    assert wi.args["model_id"] == "m-claims"
    assert wi.args["model_version"] == 2  # the PRODUCTION version, not the archived v1
    assert wi.args["model_version_urn"].endswith("model_version/m-claims@2")
    assert wi.args["input_dataset_urn"].endswith("dataset/ds-claims")
    assert wi.args["output_dataset_name"]  # derived, matches the job-name charset
    # affected URNs = the model version + the input dataset
    assert any("model_version/m-claims@2" in u for u in wi.affected_urns)
    assert any("dataset/ds-claims" in u for u in wi.affected_urns)
    # the compatibility report rides on the predicted effect
    assert wi.predicted_effect["compatibility"]["compatible"] is True
    assert wi.rationale
    # grounding actually happened against BOTH services
    exp_ops = [c["op"] for c in experiment.calls]
    ds_ops = [c["op"] for c in dataset.calls]
    assert "list_models" in exp_ops and "get_model" in exp_ops
    assert "list_datasets" in ds_ops and "get_schema" in ds_ops
    # retrieved memory surfaced as grounding evidence + a real model was invoked
    assert outcome.evidence
    assert outcome.usage["output_tokens"] > 0


async def test_inference_blocks_and_explains_when_incompatible():
    experiment = FakeExperimentReader()  # production v2 requires {amount: double}
    # dataset is MISSING the required `amount` feature (has an unrelated column)
    dataset = FakeDatasetReader(schema={
        "version_no": 1, "row_count": 20,
        "schema": {"note": {"type": "string", "nullable": True}}})
    deps = GraphDeps(llm=FakeLlm(content="ignored"), memory=FakeMemory(),
                     experiment_reader=experiment, dataset_reader=dataset,
                     prompt_params={}, obo_token="tok")

    outcome = await run_inference(deps, {"tenant_id": TENANT_A, "query": _QUERY})

    # incompatible path: NO proposal, a plain-text explanation instead
    assert outcome.write_intent is None
    assert outcome.final_text
    assert "incompatible" in outcome.final_text.lower()
    assert outcome.structured["compatibility"]["compatible"] is False


async def test_inference_blocks_when_no_production_version():
    # only an archived version exists -> nothing to run in proposal mode
    experiment = FakeExperimentReader(model={
        "model": {"id": "m-x", "name": "claims-fraud"},
        "versions": [{"model_id": "m-x", "version": 1, "stage": "archived",
                      "input_schema": None}]})
    deps = GraphDeps(llm=FakeLlm(), memory=FakeMemory(),
                     experiment_reader=experiment, dataset_reader=FakeDatasetReader(),
                     prompt_params={}, obo_token="tok")

    outcome = await run_inference(deps, {"tenant_id": TENANT_A, "query": _QUERY})

    assert outcome.write_intent is None
    assert "production" in (outcome.final_text or "").lower()
