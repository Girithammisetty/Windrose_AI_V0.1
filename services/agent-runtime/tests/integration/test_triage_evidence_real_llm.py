"""LIVE proof: case-triage reasons over an ATTACHED DOCUMENT through the real
model (ai-gateway -> Ollama) and cites it — the payoff of the evidence-reasoning
increment.

The decisive fact here lives ONLY in the attached document, NOT in the case row
projection: the row looks benign ($1,250, low), but the attached adjuster report
says the vehicle was declared a TOTAL LOSS and the invoice duplicates a prior
paid invoice. A model reasoning from the row alone cannot know this; a model that
reads the evidence can. We assert the real model's rationale reflects the
document content and/or names the source file — proving it reasoned over the
document, not just the columns.

The list->download->extract pipeline against case-service/MinIO is covered by the
unit tier (tests/unit/test_evidence_reader.py with a real download client + a
real PDF); here the reader supplies real document TEXT so the assertion targets
the one new thing this test can prove live: the real model uses it.

Gated exactly like test_agent_roster_real_llm (auto-skips without a vkey). See
that file's header for the one-line mint + run recipe.
"""

from __future__ import annotations

import os
import time

import jwt as pyjwt
import pytest

from app.adapters.fakes import FakeEvidenceReader, FakeMemory
from app.adapters.llm import AiGatewayLlmClient
from app.graphs.base import GraphDeps
from app.graphs.triage import run_triage
from tests.integration.conftest import AI_GATEWAY

pytestmark = pytest.mark.integration

VKEY = os.environ.get("AR_AI_GATEWAY_VKEY")
TENANT = os.environ.get("AR_AIGW_TENANT", "11111111-1111-4111-8111-111111111111")
JWT_PRIV_PATH = os.environ.get("AR_AIGW_JWT_PRIV", "/tmp/aigw_priv.pem")
JWT_ISS = os.environ.get("AR_AIGW_JWT_ISS", "https://identity.windrose.ai")
JWT_AUD = os.environ.get("AR_AIGW_JWT_AUD", "windrose")
JWT_KID = os.environ.get("AR_AIGW_JWT_KID", "e2e-harness-key-1")

_DISPOSITIONS = [
    {"id": "00000000-0000-4000-8000-000000000001", "code": "duplicate_invoice",
     "label": "Duplicate invoice", "active": True},
    {"id": "00000000-0000-4000-8000-000000000002", "code": "total_loss",
     "label": "Total loss", "active": True},
    {"id": "00000000-0000-4000-8000-000000000003", "code": "needs_review",
     "label": "Needs review", "active": True},
]


def _jwt_provider(tenant_id: str) -> str:
    priv = open(JWT_PRIV_PATH).read()  # noqa: SIM115
    now = int(time.time())
    return pyjwt.encode(
        {"iss": JWT_ISS, "aud": JWT_AUD, "sub": "agent-runtime",
         "tenant_id": tenant_id, "typ": "service", "scopes": ["*"],
         "iat": now, "nbf": now, "exp": now + 3600}, priv, algorithm="RS256",
        headers={"kid": JWT_KID})


class _CaseReader:
    def __init__(self, case, dispositions):
        self._case, self._d = case, dispositions

    async def get_case(self, *, tenant_id, case_id, auth_token):
        return {**self._case, "id": case_id}

    async def list_dispositions(self, *, tenant_id, auth_token):
        return self._d


async def test_triage_reasons_over_attached_document_live(require_ai_gateway):
    if not VKEY:
        pytest.skip("set AR_AI_GATEWAY_VKEY to a minted ai-gateway virtual key (nk-...)")

    llm = AiGatewayLlmClient(AI_GATEWAY, model="windrose-auto", virtual_key=VKEY,
                             jwt_provider=_jwt_provider, temperature=0.2, max_tokens=250)

    # The row looks routine; the DOCUMENT carries the decisive facts.
    case = {"id": "c-ev-1", "severity": "low",
            "display_projection": {"amount": "1250.00", "claim_type": "auto",
                                   "vendor": "ACME Auto Body"}}
    evidence = FakeEvidenceReader(docs=[{
        "id": "e1", "filename": "adjuster_report.txt", "content_type": "text/plain",
        "size_bytes": 400, "extracted": True, "note": "",
        "text": ("ADJUSTER FIELD REPORT — Claim c-ev-1. On inspection the vehicle "
                 "was declared a TOTAL LOSS (frame bent, airbags deployed). NOTE: "
                 "the submitted repair invoice INV-5540 is a DUPLICATE of invoice "
                 "INV-5540 already paid on 2026-04-02. Recommend fraud review.")}])

    deps = GraphDeps(
        llm=llm, memory=FakeMemory(),
        case_reader=_CaseReader(case, _DISPOSITIONS),
        evidence_reader=evidence, prompt_params={"persona": "SIU investigator"},
        obo_token="tok")

    outcome = await run_triage(deps, {"tenant_id": TENANT, "case_id": "c-ev-1"})

    # a real model ran
    assert outcome.usage.get("output_tokens", 0) > 0
    # the document was surfaced as grounding evidence on the outcome
    assert any(e.get("kind") == "case_evidence" for e in outcome.evidence)
    assert outcome.structured["evidence_docs"][0]["filename"] == "adjuster_report.txt"

    # the model REASONED OVER THE DOCUMENT: its rationale reflects a fact that is
    # only in the attachment (total loss / duplicate / INV-5540 / the filename) —
    # impossible to produce from the benign row alone.
    rationale = (outcome.write_intent.rationale or "").lower()
    signals = ["total loss", "duplicate", "inv-5540", "adjuster_report", "fraud"]
    assert any(s in rationale for s in signals), (
        f"rationale did not reflect the attached document: {rationale!r}")

    print("REAL EVIDENCE-GROUNDED TRIAGE:",
          "disposition=", outcome.write_intent.args.get("disposition_id"),
          "severity=", outcome.write_intent.args.get("severity"),
          "rationale=", outcome.write_intent.rationale,
          "usage=", outcome.usage)
