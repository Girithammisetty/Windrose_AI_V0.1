"""case-triage grounds on the case's ATTACHED DOCUMENTS, not just the row.

Proves the wiring of the evidence-reasoning increment end-to-end at graph level
(deterministic fake LLM): the extracted document text reaches the model prompt,
the agent is told to cite the filename, and the read documents surface on the
outcome (evidence + structured.evidence_docs + trace) for the eval judge / UI.
"""

from __future__ import annotations

from app.adapters.fakes import FakeCaseReader, FakeEvidenceReader, FakeMemory
from app.domain.ports import LlmResult
from app.graphs.base import GraphDeps
from app.graphs.triage import run_triage
from tests.conftest import TENANT_A

_DISPOSITIONS = [
    {"id": "00000000-0000-4000-8000-000000000001", "code": "duplicate_invoice",
     "label": "Duplicate invoice", "active": True},
    {"id": "00000000-0000-4000-8000-000000000002", "code": "needs_review",
     "label": "Needs review", "active": True},
]


class _CaseReaderWithDispositions(FakeCaseReader):
    def __init__(self, case, dispositions):
        super().__init__(case)
        self._dispositions = dispositions

    async def list_dispositions(self, *, tenant_id, auth_token):
        return self._dispositions


class _RecordingLlm:
    """Captures the exact user prompt so we can assert the evidence text was put
    in front of the model; returns a fixed valid triage JSON."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.user_prompts: list[str] = []

    async def chat(self, *, messages, tenant_id, response_format=None,
                   temperature=None, max_tokens=None) -> LlmResult:
        self.user_prompts.append(next(m["content"] for m in messages if m["role"] == "user"))
        return LlmResult(content=self._content, input_tokens=50, output_tokens=20,
                         model="fake-fast-small", deployment="fake")


async def test_triage_puts_evidence_text_in_prompt_and_cites_it():
    # A distinctive fact that exists ONLY in the attached document, not the row.
    evidence = FakeEvidenceReader(docs=[{
        "id": "e1", "filename": "invoice_INV-5540.pdf", "content_type": "application/pdf",
        "size_bytes": 900, "extracted": True, "note": "",
        "text": "Invoice INV-5540 for $12,500 — DUPLICATE of prior invoice INV-5540 "
                "already paid on 2026-04-02 to ACME Duplicate LLC."}])
    llm = _RecordingLlm(
        '{"severity":"high","disposition_code":"duplicate_invoice",'
        '"rationale":"Duplicate of INV-5540 per invoice_INV-5540.pdf."}')
    deps = GraphDeps(
        llm=llm, memory=FakeMemory(),
        case_reader=_CaseReaderWithDispositions(
            {"id": "c-501", "severity": "medium",
             "display_projection": {"amount": "12500.00"}}, _DISPOSITIONS),
        evidence_reader=evidence, prompt_params={"persona": "SIU"}, obo_token="tok")

    outcome = await run_triage(deps, {"tenant_id": TENANT_A, "case_id": "c-501"})

    # (1) the ACTUAL document text was placed in the model's prompt
    prompt = llm.user_prompts[0]
    assert "INV-5540" in prompt
    assert "invoice_INV-5540.pdf" in prompt
    assert "Attached case evidence documents" in prompt

    # (2) the read document surfaces on the outcome for eval/UI
    sources = [e.get("source") for e in outcome.evidence]
    assert "invoice_INV-5540.pdf" in sources
    assert any(e.get("kind") == "case_evidence" for e in outcome.evidence)
    docs = outcome.structured["evidence_docs"]
    assert docs and docs[0]["filename"] == "invoice_INV-5540.pdf" and docs[0]["extracted"]

    # (3) the grounding is visible in the trace
    assert any(t.get("event") == "evidence_grounded" for t in outcome.trace)

    # (4) it still produces the governed proposal (unchanged contract)
    assert outcome.write_intent.tool_id == "case.apply_disposition"
    assert outcome.write_intent.args["disposition_id"] == _DISPOSITIONS[0]["id"]


async def test_triage_marks_unextractable_evidence_without_hiding_it():
    """An image attachment can't be read yet (OCR follow-up) — the model must
    still be TOLD it exists rather than the document being silently dropped."""
    evidence = FakeEvidenceReader(docs=[{
        "id": "e1", "filename": "damage_photo.png", "content_type": "image/png",
        "size_bytes": 40000, "extracted": False,
        "note": "image evidence — not text-extractable (OCR is a follow-up)", "text": ""}])
    llm = _RecordingLlm(
        '{"severity":"medium","disposition_code":"needs_review",'
        '"rationale":"No readable evidence; needs manual review."}')
    deps = GraphDeps(
        llm=llm, memory=FakeMemory(),
        case_reader=_CaseReaderWithDispositions(
            {"id": "c-9", "display_projection": {}}, _DISPOSITIONS),
        evidence_reader=evidence, prompt_params={}, obo_token="tok")

    outcome = await run_triage(deps, {"tenant_id": TENANT_A, "case_id": "c-9"})
    prompt = llm.user_prompts[0]
    assert "damage_photo.png" in prompt          # the doc is named
    assert "not text-extractable" in prompt      # and its unreadability is explicit
    # not added as usable grounding evidence (no extracted text)
    assert not any(e.get("kind") == "case_evidence" for e in outcome.evidence)


async def test_triage_without_evidence_reader_is_unchanged():
    """No evidence_reader → no evidence section, classic structured-only grounding."""
    llm = _RecordingLlm(
        '{"severity":"low","disposition_code":"needs_review","rationale":"ok"}')
    deps = GraphDeps(
        llm=llm, memory=FakeMemory(),
        case_reader=_CaseReaderWithDispositions(
            {"id": "c-1", "display_projection": {}}, _DISPOSITIONS),
        prompt_params={}, obo_token="tok")
    outcome = await run_triage(deps, {"tenant_id": TENANT_A, "case_id": "c-1"})
    assert "Attached case evidence documents" not in llm.user_prompts[0]
    assert outcome.structured["evidence_docs"] == []
