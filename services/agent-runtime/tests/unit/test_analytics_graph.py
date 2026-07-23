"""analytics agent graph (ART-FR-013) — read-only. It answers a governed
data question and must NEVER emit a WriteIntent. Fills the graph-test gap for
the analytics agent (previously only touched incidentally via the catalog).

Mirrors the other per-agent graph tests: a FakeLlm makes the run deterministic,
so these assert control-flow + the read-only contract, not model quality. The
real-model path for this agent is covered by tests/integration/
test_agent_roster_real_llm.py.
"""

from __future__ import annotations

from app.adapters.fakes import FakeCaseReader, FakeEvidenceReader, FakeLlm
from app.domain.ports import LlmResult
from app.graphs.analytics import run_analytics
from app.graphs.base import GraphDeps
from tests.conftest import TENANT_A

_ANSWER = "Grounded in the governed semantic layer: 42 claims, $1.2M paid, top type = auto."


async def test_analytics_answers_and_never_writes():
    deps = GraphDeps(llm=FakeLlm(content=_ANSWER))
    outcome = await run_analytics(deps, {"tenant_id": TENANT_A,
                                         "query": "How many claims and total paid?"})

    # Read-only contract: an answer, and categorically NO write intent.
    assert outcome.final_text == _ANSWER
    assert outcome.write_intent is None
    # A model was actually invoked (usage populated).
    assert outcome.usage["output_tokens"] > 0
    assert outcome.usage["model"]


async def test_analytics_is_read_only_even_when_a_data_tool_was_used():
    """Even on the reflection path (a data tool was used), the agent stays
    read-only — it produces a grounded answer, never a WriteIntent."""
    deps = GraphDeps(llm=FakeLlm(content=_ANSWER))
    outcome = await run_analytics(deps, {"tenant_id": TENANT_A,
                                         "query": "Trend paid amount by month",
                                         "used_data_tool": True, "max_reflections": 1})
    assert outcome.final_text == _ANSWER
    assert outcome.write_intent is None
    assert outcome.usage["output_tokens"] > 0


async def test_analytics_role_grounds_the_prompt():
    """When the caller's role is known, the system prompt is role-grounded
    (ART-FR-040) — the directive is appended to the analytics system message."""
    llm = FakeLlm(content=_ANSWER)
    deps = GraphDeps(llm=llm)
    await run_analytics(deps, {"tenant_id": TENANT_A, "query": "Summarise claims",
                               "caller": {"roles": ["data-scientist"]}})
    # The single chat call carried a system message; role grounding must not have
    # crashed and a model call was made.
    assert llm.calls
    sys_msg = next(m for m in llm.calls[0]["messages"] if m["role"] == "system")
    assert "semantic layer" in sys_msg["content"].lower()


class _RecordingLlm:
    """Captures the exact user prompt so we can assert case + evidence text
    actually reached the model (not just the raw free-text question)."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.user_prompts: list[str] = []

    async def chat(self, *, messages, tenant_id, response_format=None,
                   temperature=None, max_tokens=None) -> LlmResult:
        self.user_prompts.append(next(m["content"] for m in messages if m["role"] == "user"))
        return LlmResult(content=self._content, input_tokens=50, output_tokens=20, model="fake")


async def test_analytics_grounds_on_the_case_when_a_case_id_is_supplied():
    """The case-detail Copilot drawer sends a case_id (resolved from the page's
    context URN) — the graph must fetch that case + its evidence and put both
    in front of the model, not answer generically with no context (the bug: a
    from-scratch demo tenant's Copilot said "I'm unable to access real-time
    data" because this grounding didn't exist yet)."""
    case = {"id": "case-1", "denial_id": "DN-3901", "payer_name": "Georgia Medicaid CMO",
            "appeal_status": "not_appealed", "appeal_deadline_days": 9}
    llm = _RecordingLlm("The DN-3901 denial is a precert issue for Georgia Medicaid CMO.")
    deps = GraphDeps(
        llm=llm, case_reader=FakeCaseReader(case),
        evidence_reader=FakeEvidenceReader(
            [{"filename": "denial-letter.pdf", "content_type": "application/pdf",
              "text": "Precertification absent for cardiac catheterization.", "extracted": True}]))

    outcome = await run_analytics(deps, {
        "tenant_id": TENANT_A, "case_id": "case-1",
        "query": "summarize this denial and the appeal history for this payer"})

    assert outcome.final_text == llm._content
    assert outcome.write_intent is None
    assert llm.user_prompts, "the model must have been called"
    prompt = llm.user_prompts[0]
    # The case's real fields reached the prompt...
    assert "DN-3901" in prompt
    assert "Georgia Medicaid CMO" in prompt
    # ...and so did the evidence document text, not just the row projection.
    assert "Precertification absent for cardiac catheterization" in prompt
    # ...alongside the user's actual question (not silently dropped).
    assert "summarize this denial" in prompt
    # Grounding is auditable in the run trace, not silent.
    assert any(e.get("event") == "case_grounded" for e in outcome.trace)


async def test_analytics_without_a_case_id_behaves_exactly_as_before():
    """No case_id (e.g. a general /data question) -> ground is a no-op and the
    prompt is exactly the user's raw query, unchanged from pre-grounding
    behaviour."""
    llm = _RecordingLlm(_ANSWER)
    deps = GraphDeps(llm=llm)
    await run_analytics(deps, {"tenant_id": TENANT_A, "query": "How many claims total?"})
    assert llm.user_prompts == ["How many claims total?"]
