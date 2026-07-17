"""case-triage LangGraph produces a disposition WRITE INTENT (never a direct
write) grounded in case + memory (ART-FR-040/041)."""

from __future__ import annotations

from app.adapters.fakes import FakeCaseReader, FakeLlm, FakeMemory
from app.graphs.base import GraphDeps
from app.graphs.triage import run_triage
from tests.conftest import TENANT_A


async def test_triage_produces_write_intent():
    deps = GraphDeps(llm=FakeLlm(), memory=FakeMemory(results=[{"content": "resolved dup case"}]),
                     case_reader=FakeCaseReader(), prompt_params={"persona": "SIU investigator"},
                     obo_token="tok")
    outcome = await run_triage(deps, {"tenant_id": TENANT_A, "case_id": "c-91"})
    assert outcome.write_intent is not None
    wi = outcome.write_intent
    assert wi.tool_id == "case.apply_disposition"
    assert wi.tier == "write-proposal"
    assert wi.args["case_id"] == "c-91"
    assert wi.args["severity"] in ("low", "medium", "high", "critical")
    assert wi.affected_urns == [f"wr:{TENANT_A}:case:case/c-91"]
    assert outcome.usage["output_tokens"] > 0  # a model was invoked


async def test_triage_defensive_on_bad_json():
    deps = GraphDeps(llm=FakeLlm(content="not json at all"), memory=FakeMemory(),
                     case_reader=FakeCaseReader(), prompt_params={})
    outcome = await run_triage(deps, {"tenant_id": TENANT_A, "case_id": "c-1"})
    # falls back to a valid proposal rather than crashing
    assert outcome.write_intent is not None
    assert outcome.write_intent.args["severity"] == "medium"
