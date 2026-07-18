"""governance agent graph (ART-FR-040, US-10) — threshold-driven retrain
proposals. Given drift/correction signals it either opens a RETRAIN proposal
(a WriteIntent, never a direct write) or declines. Fills the graph-test gap for
the governance agent (previously only touched incidentally via the catalog).

FakeLlm makes the run deterministic: the decision to propose is threshold-driven
(not model-driven), so these assert the governance logic + the write-vs-no-write
contract. The real-model path is covered by tests/integration/
test_agent_roster_real_llm.py.
"""

from __future__ import annotations

from app.adapters.fakes import FakeLlm
from app.graphs.base import GraphDeps
from app.graphs.governance import RETRAIN_TOOL_ID, run_governance
from tests.conftest import TENANT_A

_MODEL_URN = f"wr:{TENANT_A}:experiment:model/claims-fraud"


def _deps(content: str = "Drift exceeds threshold; retrain is warranted.") -> GraphDeps:
    return GraphDeps(llm=FakeLlm(content=content))


async def test_governance_opens_retrain_proposal_when_drift_exceeds_threshold():
    outcome = await run_governance(_deps(), {
        "tenant_id": TENANT_A, "model_urn": _MODEL_URN, "drift_threshold": 0.3,
        "signals": {"drift_score": 0.45, "correction_count": 3}})

    wi = outcome.write_intent
    assert wi is not None
    assert wi.tool_id == RETRAIN_TOOL_ID == "mlops.open_retrain"
    assert wi.tier == "write-proposal"
    assert wi.side_effects == "reversible"
    assert wi.args["model_urn"] == _MODEL_URN
    assert wi.args["reason"] == "drift_exceeded"
    assert wi.affected_urns == [_MODEL_URN]
    assert wi.rationale.strip()                       # grounded rationale attached
    assert outcome.usage["output_tokens"] > 0          # a model was invoked


async def test_governance_opens_proposal_on_correction_volume_alone():
    """Even with drift below threshold, >= 20 human corrections warrant a retrain
    (the human-signal arm of the OR)."""
    outcome = await run_governance(_deps(), {
        "tenant_id": TENANT_A, "model_urn": _MODEL_URN, "drift_threshold": 0.3,
        "signals": {"drift_score": 0.05, "correction_count": 25}})
    assert outcome.write_intent is not None
    assert outcome.write_intent.tool_id == "mlops.open_retrain"


async def test_governance_declines_when_signals_are_within_threshold():
    outcome = await run_governance(_deps(), {
        "tenant_id": TENANT_A, "model_urn": _MODEL_URN, "drift_threshold": 0.3,
        "signals": {"drift_score": 0.10, "correction_count": 2}})
    assert outcome.write_intent is None                # no proposal
    assert "no retrain" in outcome.final_text.lower()
    assert outcome.usage["output_tokens"] > 0          # the model still summarised


async def test_governance_defensive_rationale_when_model_returns_empty():
    """If the model returns an empty summary, the rationale falls back to a
    deterministic drift/threshold statement rather than being blank."""
    outcome = await run_governance(_deps(content=""), {
        "tenant_id": TENANT_A, "model_urn": _MODEL_URN, "drift_threshold": 0.3,
        "signals": {"drift_score": 0.9, "correction_count": 1}})
    wi = outcome.write_intent
    assert wi is not None
    assert wi.rationale.strip()                        # never blank
    assert "0.9" in wi.rationale or "threshold" in wi.rationale.lower()
