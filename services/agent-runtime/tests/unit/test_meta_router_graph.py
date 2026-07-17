"""meta-router classifies a request and delegates to the matching specialist
agent, reusing the SAME GraphDeps so the delegate's own grounding/write-intent
logic runs unchanged (ART-FR-040, §8.4)."""

from __future__ import annotations

from app.adapters.fakes import FakeLlm, FakeMemory
from app.domain.ports import LlmResult
from app.graphs.base import GraphDeps
from app.graphs.meta_router import run_meta_router
from tests.conftest import TENANT_A


class _SequencedLlm:
    """Returns a different response per successive call (classify, then the
    delegate's own LLM call) — FakeLlm alone can't model two distinct prompts."""

    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls: list[dict] = []

    async def chat(self, *, messages, tenant_id, response_format=None,
                   temperature=None, max_tokens=None) -> LlmResult:
        self.calls.append({"messages": messages, "tenant_id": tenant_id})
        idx = min(len(self.calls) - 1, len(self._contents) - 1)
        return LlmResult(content=self._contents[idx], input_tokens=10,
                         output_tokens=5, model="fake-fast-small", deployment="fake")


async def test_meta_router_delegates_to_governance_and_forwards_write_intent():
    llm = _SequencedLlm([
        '{"agent_key":"governance","confidence":0.9,'
        '"rationale":"drift/retrain question"}',
        "Drift exceeds threshold; retrain warranted.",
    ])
    deps = GraphDeps(llm=llm, memory=FakeMemory(), prompt_params={}, obo_token="tok")

    outcome = await run_meta_router(deps, {
        "tenant_id": TENANT_A,
        "query": "Model drift check: drift_score 0.45, 25 corrections.",
        "signals": {"drift_score": 0.45, "correction_count": 25},
        "model_urn": f"wr:{TENANT_A}:model:model/claims-fraud",
    })

    assert outcome.structured["routed_to"] == "governance"
    assert outcome.final_text.startswith("[routed to governance] ")
    wi = outcome.write_intent
    assert wi is not None
    assert wi.tool_id == "mlops.open_retrain"
    # both LLM calls (classify + delegate) were made and metered
    assert len(llm.calls) == 2
    assert outcome.usage["output_tokens"] == 10  # 5 (classify) + 5 (delegate)


async def test_meta_router_falls_back_to_analytics_on_unparsable_classification():
    # Non-JSON classify response -> falls back to "analytics"; analytics's own
    # node just consumes the SAME text as a freeform answer (no format clash).
    llm = FakeLlm(content="I'm not sure how to route this, but here's an answer.")
    deps = GraphDeps(llm=llm, memory=FakeMemory(), prompt_params={}, obo_token="tok")

    outcome = await run_meta_router(deps, {
        "tenant_id": TENANT_A, "query": "what does this even mean"})

    assert outcome.structured["routed_to"] == "analytics"
    assert outcome.write_intent is None  # analytics is read-only
    assert outcome.final_text.startswith("[routed to analytics] ")
    assert len(llm.calls) == 2  # classify + delegate, both hit the real double
