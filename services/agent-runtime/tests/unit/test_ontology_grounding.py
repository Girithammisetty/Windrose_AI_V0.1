"""Knowledge Spine WS1 — agents ground reasoning in the workspace's governed
ontology (docs/initiatives/knowledge-spine-ontology.md).

Proves, at graph level with a deterministic recording LLM, that the workspace's
governed domain model (entity types + attributes + typed relationships) is placed
in front of the model in BOTH the triage and the shared persona-copilot graphs;
that grounding is best-effort (a read error is surfaced in the trace, never
swallowed, and the run still completes); and that with no ontology source the
prompt is unchanged. Mirrors the evidence-grounding test harness.
"""

from __future__ import annotations

from app.adapters.fakes import FakeCaseReader, FakeMemory
from app.domain.ports import LlmResult
from app.graphs.base import GraphDeps
from app.graphs.persona_copilot import run_persona_copilot
from app.graphs.triage import _format_ontology, run_triage
from tests.conftest import TENANT_A

_DISPOSITIONS = [
    {"id": "00000000-0000-4000-8000-000000000001", "code": "duplicate_invoice",
     "label": "Duplicate invoice", "active": True},
    {"id": "00000000-0000-4000-8000-000000000002", "code": "needs_review",
     "label": "Needs review", "active": True},
]

_ONTOLOGY = [
    {"entity_key": "vendor", "name": "Vendor", "description": "A supplier paid via AP.",
     "attributes": [{"name": "tenure_band", "data_type": "string",
                     "description": "new_under_1y | 1_3_years | over_3_years"}],
     "relationships": [{"name": "invoices", "target": "invoice", "cardinality": "has_many"}]},
    {"entity_key": "invoice", "name": "Invoice", "description": "",
     "attributes": [{"name": "amount", "data_type": "number"}],
     "relationships": [{"name": "vendor", "target": "vendor", "cardinality": "belongs_to"}]},
]


class _CaseReaderWithDispositions(FakeCaseReader):
    def __init__(self, case, dispositions):
        super().__init__(case)
        self._dispositions = dispositions

    async def list_dispositions(self, *, tenant_id, auth_token):
        return self._dispositions


class _FakeOntologyReader:
    """A dataset_reader exposing list_ontology_types (the only method the ground
    node calls). Records the workspace it was asked for."""

    def __init__(self, types):
        self._types = types
        self.calls: list[str] = []

    async def list_ontology_types(self, *, tenant_id, workspace_id, auth_token):
        self.calls.append(workspace_id)
        return self._types


class _ErroringOntologyReader:
    async def list_ontology_types(self, *, tenant_id, workspace_id, auth_token):
        raise RuntimeError("dataset-service unreachable")


class _RecordingLlm:
    def __init__(self, content: str) -> None:
        self._content = content
        self.user_prompts: list[str] = []

    async def chat(self, *, messages, tenant_id, response_format=None,
                   temperature=None, max_tokens=None) -> LlmResult:
        self.user_prompts.append(next(m["content"] for m in messages if m["role"] == "user"))
        return LlmResult(content=self._content, input_tokens=50, output_tokens=20,
                         model="fake-fast-small", deployment="fake")


def _triage_deps(*, dataset_reader=None, case=None):
    return GraphDeps(
        llm=_RecordingLlm(
            '{"severity":"high","disposition_code":"duplicate_invoice",'
            '"rationale":"Duplicate vendor invoice."}'),
        memory=FakeMemory(),
        case_reader=_CaseReaderWithDispositions(
            case or {"id": "c-501", "severity": "medium", "workspace_id": "ws-1",
                     "display_projection": {"amount": "12500.00"}}, _DISPOSITIONS),
        dataset_reader=dataset_reader, prompt_params={"persona": "SIU"}, obo_token="tok")


async def test_triage_grounds_in_workspace_ontology():
    reader = _FakeOntologyReader(_ONTOLOGY)
    deps = _triage_deps(dataset_reader=reader)
    outcome = await run_triage(deps, {"tenant_id": TENANT_A, "case_id": "c-501"})

    prompt = deps.llm.user_prompts[0]
    # (1) the governed domain model is in front of the model — types, attribute
    # semantics (incl. the enum in the description), and typed relationships.
    assert "Governed domain model" in prompt
    assert "Vendor (vendor)" in prompt
    assert "tenure_band" in prompt
    assert "new_under_1y" in prompt                      # attribute-description enum
    assert "invoices: has_many invoice" in prompt
    # (2) it was fetched for the CASE's workspace (grounded, not guessed)
    assert reader.calls == ["ws-1"]
    # (3) the grounding is visible in the trace
    assert any(t.get("event") == "ontology_grounded" and t.get("types") == 2
               for t in outcome.trace)
    # (4) the governed proposal contract is unchanged
    assert outcome.write_intent.tool_id == "case.apply_disposition"


async def test_triage_without_ontology_source_is_unchanged():
    # No dataset_reader → no domain-model block; classic grounding still works.
    deps = _triage_deps(dataset_reader=None)
    outcome = await run_triage(deps, {"tenant_id": TENANT_A, "case_id": "c-501"})
    assert "Governed domain model" not in deps.llm.user_prompts[0]
    assert outcome.write_intent.tool_id == "case.apply_disposition"


async def test_triage_ontology_read_error_is_surfaced_not_swallowed():
    deps = _triage_deps(dataset_reader=_ErroringOntologyReader())
    outcome = await run_triage(deps, {"tenant_id": TENANT_A, "case_id": "c-501"})
    # Best-effort: the failure is recorded in the trace, and the run still finishes.
    assert any(t.get("event") == "ontology_grounding_failed" for t in outcome.trace)
    assert "Governed domain model" not in deps.llm.user_prompts[0]
    assert outcome.write_intent.tool_id == "case.apply_disposition"


async def test_persona_copilot_grounds_in_workspace_ontology():
    reader = _FakeOntologyReader(_ONTOLOGY)
    deps = GraphDeps(
        llm=_RecordingLlm(
            '{"severity":"high","disposition_code":"deny_no_error_found",'
            '"rationale":"per governed model"}'),
        memory=FakeMemory(results=[]),
        case_reader=_CaseReaderWithDispositions(
            {"id": "c-91", "severity": "medium", "workspace_id": "ws-1"},
            [{"id": "disp-1", "code": "deny_no_error_found", "label": "Deny"}]),
        dataset_reader=reader,
        prompt_params={"persona": "Analyst", "propose_tool": "case.apply_disposition"},
        obo_token="tok")
    await run_persona_copilot(deps, {"tenant_id": TENANT_A, "case_id": "c-91",
                                     "workspace_id": "ws-1"})
    prompt = deps.llm.user_prompts[0]
    assert "Governed domain model" in prompt and "Vendor (vendor)" in prompt
    assert reader.calls == ["ws-1"]


def test_format_ontology_empty_and_render():
    assert _format_ontology([]) == ""
    out = _format_ontology(_ONTOLOGY)
    assert "Governed domain model" in out
    assert "Vendor (vendor): A supplier paid via AP." in out
    assert "- tenure_band [string] - new_under_1y | 1_3_years | over_3_years" in out
    assert "-> invoices: has_many invoice" in out
    # an empty description doesn't leave a dangling colon
    assert "Invoice (invoice)\n" in out
