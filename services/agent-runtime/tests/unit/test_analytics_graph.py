"""analytics agent graph (ART-FR-013) — read-only. It answers a governed
data question and must NEVER emit a WriteIntent. Fills the graph-test gap for
the analytics agent (previously only touched incidentally via the catalog).

Mirrors the other per-agent graph tests: a FakeLlm makes the run deterministic,
so these assert control-flow + the read-only contract, not model quality. The
real-model path for this agent is covered by tests/integration/
test_agent_roster_real_llm.py.
"""

from __future__ import annotations

from app.adapters.fakes import FakeLlm
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
