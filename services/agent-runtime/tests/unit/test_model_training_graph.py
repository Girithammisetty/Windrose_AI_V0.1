"""model-training LangGraph produces a training-run WRITE INTENT (never a direct
write) grounded in the algorithm-template schema + prior experiment history +
memory (ART-FR-040). Mirrors test_triage_graph."""

from __future__ import annotations

from app.adapters.fakes import (
    FakeExperimentReader,
    FakeLlm,
    FakeMemory,
    FakePipelineReader,
)
from app.graphs.base import GraphDeps
from app.graphs.model_training import TRAINING_TOOL_ID, run_model_training
from tests.conftest import TENANT_A

_PLAN_JSON = (
    '{"hyperparameters": {"n_estimators": 5000, "max_depth": 4, "learning_rate": 0.05},'
    ' "label_column": "is_fraud", "feature_columns": ["amount", "merchant"],'
    ' "rationale": "History shows depth 4 + lr 0.05 topped accuracy on this algorithm."}'
)


def _deps(**over):
    base = dict(
        llm=FakeLlm(content=_PLAN_JSON),
        memory=FakeMemory(results=[{"content": "prior xgboost fraud run hit 0.94 acc"}]),
        pipeline_reader=FakePipelineReader(),
        experiment_reader=FakeExperimentReader(
            runs=[{"metrics": {"accuracy": 0.94}, "params": {"max_depth": "4"},
                   "status": "succeeded"}]),
        prompt_params={}, obo_token="tok")
    base.update(over)
    return GraphDeps(**base)


async def test_model_training_produces_write_intent():
    deps = _deps()
    outcome = await run_model_training(deps, {
        "tenant_id": TENANT_A, "query": "Train an xgboost classifier on the claims "
        "dataset to predict fraud", "workspace_id": "ws-1"})

    assert outcome.write_intent is not None
    wi = outcome.write_intent
    assert wi.tool_id == TRAINING_TOOL_ID == "pipeline.template.create_from_algorithm"
    assert wi.tier == "write-proposal"
    assert wi.args["algorithm"] == "xgboost"          # resolved from the NL request
    assert wi.args["mode"] == "train"
    # hyperparameters were coerced/clamped to the schema (n_estimators max is 2000)
    assert wi.args["params"]["n_estimators"] == 2000
    assert wi.args["params"]["max_depth"] == 4
    assert wi.args["params"]["label_column"] == "is_fraud"
    assert wi.args["params"]["feature_columns"] == ["amount", "merchant"]
    assert wi.args["dataset_refs"]["TRAIN"].startswith(f"wr:{TENANT_A}:dataset:")
    assert wi.args["workspace_id"] == "ws-1"
    assert wi.affected_urns == [f"wr:{TENANT_A}:pipeline:training/xgboost"]
    assert wi.rationale.strip()                        # non-empty grounded rationale
    assert outcome.usage["output_tokens"] > 0          # a model was invoked
    assert outcome.evidence                            # grounding memories surfaced


async def test_model_training_called_grounding_tools():
    deps = _deps()
    await run_model_training(deps, {
        "tenant_id": TENANT_A, "query": "train xgboost to predict fraud"})
    assert any(c["op"] == "get_algorithm" for c in deps.pipeline_reader.calls)
    assert any(c["op"] == "best_runs" for c in deps.experiment_reader.calls)
    assert deps.memory.calls  # RAG grounding was attempted


async def test_model_training_defensive_on_bad_json():
    deps = _deps(llm=FakeLlm(content="not json at all"))
    outcome = await run_model_training(deps, {
        "tenant_id": TENANT_A, "query": "train a random forest classifier"})
    # falls back to a valid proposal rather than crashing
    assert outcome.write_intent is not None
    assert outcome.write_intent.args["algorithm"] == "random_forest"
    assert outcome.write_intent.args["params"]["label_column"] == "label"
