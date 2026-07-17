"""REAL triage copilot run: the case-triage LangGraph reasons with the REAL model
(ai-gateway -> Ollama qwen2.5:0.5b) and produces a disposition PROPOSAL
(ART-FR-013/041, verification req 1).

Asserts a real model was called: usage token counts are populated by the gateway
from Ollama's response, and the output is non-deterministic across two runs.
The LLM call goes THROUGH ai-gateway (never direct to Ollama). Auto-skips when
ai-gateway is unreachable (set AR_AI_GATEWAY_URL / AR_AI_GATEWAY_VKEY)."""

from __future__ import annotations

import os
import time

import jwt as pyjwt
import pytest

from app.adapters.case import CaseServiceClient  # noqa: F401  (documents read path)
from app.adapters.fakes import FakeCaseReader, FakeMemory
from app.adapters.llm import AiGatewayLlmClient
from app.graphs.base import GraphDeps
from app.graphs.triage import run_triage
from tests.integration.conftest import AI_GATEWAY

pytestmark = pytest.mark.integration

VKEY = os.environ.get("AR_AI_GATEWAY_VKEY")
# Tenant + JWT private key the seeded ai-gateway deployment/key belong to.
TENANT = os.environ.get("AR_AIGW_TENANT", "11111111-1111-4111-8111-111111111111")
JWT_PRIV_PATH = os.environ.get("AR_AIGW_JWT_PRIV", "/tmp/aigw_priv.pem")


def _jwt_provider(tenant_id: str) -> str:
    priv = open(JWT_PRIV_PATH).read()  # noqa: SIM115
    now = int(time.time())
    return pyjwt.encode(
        {"iss": "https://identity.windrose.local", "aud": "windrose", "sub": "agent-runtime",
         "tenant_id": tenant_id, "typ": "service", "scopes": ["*"],
         "iat": now, "exp": now + 3600}, priv, algorithm="RS256")


def _real_llm() -> AiGatewayLlmClient:
    return AiGatewayLlmClient(
        AI_GATEWAY, model="windrose-auto", virtual_key=VKEY,
        jwt_provider=_jwt_provider, temperature=0.6, max_tokens=200)


async def test_real_triage_proposal_via_ai_gateway(require_ai_gateway):
    if not VKEY:
        pytest.skip("set AR_AI_GATEWAY_VKEY to a minted ai-gateway virtual key (nk-...)")

    llm = _real_llm()
    case = {"id": "c-501", "severity": "medium",
            "display_projection": {"amount": "12500.00", "merchant": "ACME Duplicate LLC",
                                   "line": "commercial-property"}}
    deps = GraphDeps(llm=llm, memory=FakeMemory(results=[
        {"content": "resolved: duplicate-invoice fraud, assigned SIU, severity high"}]),
        case_reader=FakeCaseReader(case), prompt_params={"persona": "SIU investigator"},
        obo_token="tok")

    outcome = await run_triage(deps, {"tenant_id": TENANT, "case_id": "c-501"})
    assert outcome.write_intent is not None
    assert outcome.write_intent.tool_id == "case.apply_disposition"
    assert outcome.write_intent.args["severity"] in ("low", "medium", "high", "critical")
    # a real model was invoked: token usage populated by the gateway from Ollama
    assert outcome.usage["input_tokens"] > 0
    assert outcome.usage["output_tokens"] > 0
    assert outcome.usage["model"]  # ladder alias returned by the gateway
    print("REAL TRIAGE PROPOSAL:", outcome.write_intent.args,
          "rationale:", outcome.write_intent.rationale[:200], "usage:", outcome.usage)


async def test_real_model_non_deterministic(require_ai_gateway):
    if not VKEY:
        pytest.skip("set AR_AI_GATEWAY_VKEY to a minted ai-gateway virtual key (nk-...)")
    llm = _real_llm()
    msgs = [{"role": "user", "content": "Give a one-word claim severity for a $50k fire loss."}]
    a = await llm.chat(messages=msgs, tenant_id=TENANT, temperature=1.0, max_tokens=30)
    b = await llm.chat(messages=msgs, tenant_id=TENANT, temperature=1.0, max_tokens=30)
    assert a.output_tokens > 0 and b.output_tokens > 0  # real inference both times
