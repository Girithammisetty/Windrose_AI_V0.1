"""persona-copilot (BRD 53) — the shared config-driven graph tenant custom
agents run on. Proves: it proposes ONLY the configured allow-listed tool with a
real disposition_id; it is read-only (no write intent) when no propose_tool is
configured; and the tenant system_prompt + persona flow into the reasoning."""

from __future__ import annotations

from app.adapters.fakes import FakeLlm, FakeMemory
from app.graphs.base import GraphDeps
from app.graphs.persona_copilot import run_persona_copilot
from tests.conftest import TENANT_A

_LLM = ('{"severity": "high", "disposition_code": "deny_no_error_found", '
        '"rationale": "Delivery confirmed to the address on file for the third time."}')


class _CaseReaderWithDispositions:
    async def get_case(self, *, tenant_id, case_id, auth_token) -> dict:
        return {"id": case_id, "severity": "medium", "workspace_id": "ws-1"}

    async def list_dispositions(self, *, tenant_id, auth_token) -> list[dict]:
        return [{"id": "disp-1", "code": "deny_no_error_found", "label": "Deny"},
                {"id": "disp-2", "code": "resolve_cardholder_favor", "label": "Refund"}]


def _deps(prompt_params):
    return GraphDeps(
        llm=FakeLlm(content=_LLM), memory=FakeMemory(results=[]),
        case_reader=_CaseReaderWithDispositions(),
        prompt_params=prompt_params, obo_token="tok")


async def test_persona_copilot_proposes_allowlisted_tool():
    deps = _deps({"persona": "Dispute Intake Analyst",
                  "system_prompt": "Prioritise Reg E deadlines; be conservative.",
                  "propose_tool": "case.apply_disposition"})
    out = await run_persona_copilot(deps, {
        "tenant_id": TENANT_A, "case_id": "c-91", "workspace_id": "ws-1"})

    wi = out.write_intent
    assert wi is not None
    assert wi.tool_id == "case.apply_disposition"
    assert wi.tier == "write-proposal"
    assert wi.required_action == "case.case.update"          # caller-gate applies
    assert wi.args["disposition_id"] == "disp-1"             # resolved from catalog
    assert wi.args["severity"] == "high"
    assert wi.workspace_id == "ws-1"
    assert out.structured["persona"] == "Dispute Intake Analyst"
    assert out.structured["advisory"] is False


async def test_persona_copilot_is_advisory_without_propose_tool():
    deps = _deps({"persona": "Compliance Auditor",
                  "system_prompt": "Explain the recommended disposition; never act."})
    out = await run_persona_copilot(deps, {
        "tenant_id": TENANT_A, "case_id": "c-91", "workspace_id": "ws-1"})
    assert out.write_intent is None            # read-only: no governed write
    assert out.structured["advisory"] is True
    assert "recommend" in out.final_text.lower()


async def test_persona_copilot_unknown_propose_tool_fails_safe():
    """A propose_tool the shared graph doesn't know how to safely emit degrades
    to advisory rather than fabricating an unsafe write intent."""
    deps = _deps({"persona": "x", "propose_tool": "some.dangerous.tool"})
    out = await run_persona_copilot(deps, {
        "tenant_id": TENANT_A, "case_id": "c-91", "workspace_id": "ws-1"})
    assert out.write_intent is None
