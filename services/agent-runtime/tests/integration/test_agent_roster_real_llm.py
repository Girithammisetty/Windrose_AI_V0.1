"""PER-AGENT live regression: every published catalog agent reasons through the
REAL model path (ai-gateway -> Ollama) and returns a well-formed GOVERNED outcome
(a WriteIntent for the proposal agents; a grounded answer for the read-only one).

Why this exists
---------------
Before this, only `case-triage` was exercised against a real model
(test_triage_real_llm). Every other agent was covered at the graph level with a
FAKE llm. This closes that gap: it drives EACH agent's real `run_*` graph with a
real `AiGatewayLlmClient`, so a regression in any agent's real prompt/grounding —
not just its control flow — is caught.

Grounding readers stay faked (they supply deterministic case/schema/catalog data
the model reasons over); the LLM call is the REAL one, through ai-gateway
(budget + guardrails + metering + model ladder), never direct to Ollama.

Coverage guard
--------------
`test_roster_covers_every_published_agent` fails if a new published agent is
added to the catalog without a real-LLM entry here — so "all agents are
regression-tested against a real model" stays TRUE as the roster grows.

Running it live
---------------
Gated exactly like test_triage_real_llm (auto-skips otherwise). Against a booted
local stack (deploy/local/up.sh), mint a tenant-scoped virtual key first, then run
— the JWT is signed with the harness IdP key the gateway trusts:

    TEN=<tenant uuid, e.g. from services/ui-web/tests-live/.live-context.json>
    VKEY=$(.venv/bin/python ../../deploy/e2e/lib/seed.py aigw "$TEN")   # mints + prints nk-...

    AR_AI_GATEWAY_URL=http://localhost:8312 \
    AR_AI_GATEWAY_VKEY="$VKEY" \
    AR_AIGW_TENANT="$TEN" \
    AR_AIGW_JWT_PRIV=../../deploy/e2e/keys/idp_private.pem \
    uv run pytest tests/integration/test_agent_roster_real_llm.py -v -s -m integration

Verified 2026-07-17: all 9 agents PASS live (real fast-small/qwen2.5:0.5b tokens,
each producing its expected governed WriteIntent / grounded answer).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import jwt as pyjwt
import pytest

from app.adapters.fakes import (
    FakeChartCatalog,
    FakeDatasetReader,
    FakeExperimentReader,
    FakeIngestionReader,
    FakeMemory,
    FakePipelineReader,
    FakePipelineWriter,
    FakeSemanticReader,
)
from app.adapters.fakes import FakeCaseReader
from app.adapters.llm import AiGatewayLlmClient
from app.agents.catalog import CATALOG
from app.graphs.base import GraphDeps
from app.graphs.analytics import run_analytics
from app.graphs.dashboard_designer import run_dashboard_designer
from app.graphs.governance import run_governance
from app.graphs.inference_agent import run_inference
from app.graphs.meta_router import run_meta_router
from app.graphs.ml_engineer import run_ml_engineer
from app.graphs.model_training import run_model_training
from app.graphs.onboarding import run_onboarding
from app.graphs.triage import run_triage
from tests.integration.conftest import AI_GATEWAY

pytestmark = pytest.mark.integration

VKEY = os.environ.get("AR_AI_GATEWAY_VKEY")
TENANT = os.environ.get("AR_AIGW_TENANT", "11111111-1111-4111-8111-111111111111")
JWT_PRIV_PATH = os.environ.get("AR_AIGW_JWT_PRIV", "/tmp/aigw_priv.pem")
# The ai-gateway verifies X-Windrose-JWT against the IdP JWKS, so the token MUST
# carry the signing key's `kid` header and the issuer/audience the gateway trusts.
# Defaults match the local dev/e2e harness key (deploy/e2e/keys/idp_private.pem,
# kid "e2e-harness-key-1"); override for a different signer.
JWT_ISS = os.environ.get("AR_AIGW_JWT_ISS", "https://identity.windrose.ai")
JWT_AUD = os.environ.get("AR_AIGW_JWT_AUD", "windrose")
JWT_KID = os.environ.get("AR_AIGW_JWT_KID", "e2e-harness-key-1")

_MODEL_URN = f"wr:{TENANT}:experiment:model/claims-fraud"
_SCHEMA = {"version_no": 3, "row_count": 26,
           "schema": {"amount": {"type": "double"}, "disposition": {"type": "string"}}}
_REGISTRY_MODELS = [{"id": "m-scorer", "name": "cd-disposition-scorer",
                     "urn": "wr:t:experiment:model/m-scorer"}]
_REGISTRY_DETAIL = {"model": _REGISTRY_MODELS[0],
                    "versions": [{"model_id": "m-scorer", "version": 1,
                                  "mlflow_run_id": "mlrun-run-1", "stage": "none"}]}


def _jwt_provider(tenant_id: str) -> str:
    priv = open(JWT_PRIV_PATH).read()  # noqa: SIM115
    now = int(time.time())
    return pyjwt.encode(
        {"iss": JWT_ISS, "aud": JWT_AUD, "sub": "agent-runtime",
         "tenant_id": tenant_id, "typ": "service", "scopes": ["*"],
         "iat": now, "nbf": now, "exp": now + 3600}, priv, algorithm="RS256",
        headers={"kid": JWT_KID})


def _real_llm() -> AiGatewayLlmClient:
    return AiGatewayLlmClient(
        AI_GATEWAY, model="windrose-auto", virtual_key=VKEY,
        jwt_provider=_jwt_provider, temperature=0.4, max_tokens=220)


class _CaseReaderWithDispositions(FakeCaseReader):
    """case-triage resolves the model's chosen disposition_code against the
    tenant's REAL disposition catalog (code -> required id). The base
    FakeCaseReader has no `list_dispositions`, so triage would see an empty
    catalog and fail to resolve — mirror the platform by supplying one."""

    def __init__(self, case: dict, dispositions: list[dict]) -> None:
        super().__init__(case)
        self._dispositions = dispositions

    async def list_dispositions(self, *, tenant_id, auth_token) -> list[dict]:
        return self._dispositions


_DISPOSITIONS = [
    {"id": "00000000-0000-4000-8000-000000000001", "code": "duplicate_invoice",
     "label": "Duplicate invoice"},
    {"id": "00000000-0000-4000-8000-000000000002", "code": "approve_payment",
     "label": "Approve payment"},
    {"id": "00000000-0000-4000-8000-000000000003", "code": "needs_review",
     "label": "Needs review"},
]


# ---- the roster --------------------------------------------------------------
# mode:
#   "proposal"  -> a WriteIntent with `tool_id` is REQUIRED (defensive fallbacks
#                  or deterministic gating guarantee one even on a weak model)
#   "read_only" -> NO WriteIntent; a grounded final_text is required
#   "governed"  -> either a WriteIntent (of tool_id) OR an honest final_text is
#                  acceptable (LLM-dependent structured output, e.g. an
#                  ml-engineer failure report or a meta-router fallback route)
@dataclass(slots=True)
class Case:
    agent_key: str
    run: Callable[..., Any]
    deps: Callable[[Any], GraphDeps]
    inputs: dict
    mode: str
    tool_id: str | None = None


ROSTER: list[Case] = [
    Case("case-triage", run_triage,
         lambda llm: GraphDeps(llm=llm, memory=FakeMemory(results=[
             {"content": "resolved: duplicate-invoice fraud, assigned SIU, severity high"}]),
             case_reader=_CaseReaderWithDispositions({"id": "c-501", "severity": "medium",
                 "display_projection": {"amount": "12500.00", "merchant": "ACME Duplicate LLC",
                                        "line": "commercial-property"}}, _DISPOSITIONS),
             prompt_params={"persona": "SIU investigator"}, obo_token="tok"),
         {"tenant_id": TENANT, "case_id": "c-501"}, "proposal", "case.apply_disposition"),

    Case("governance", run_governance,
         lambda llm: GraphDeps(llm=llm),
         {"tenant_id": TENANT, "model_urn": _MODEL_URN, "drift_threshold": 0.3,
          "signals": {"drift_score": 0.45, "correction_count": 25}},
         "proposal", "mlops.open_retrain"),

    Case("analytics", run_analytics,
         lambda llm: GraphDeps(llm=llm),
         {"tenant_id": TENANT, "query": "How many claims and what is the total paid amount?"},
         "read_only"),

    Case("onboarding", run_onboarding,
         lambda llm: GraphDeps(llm=llm, memory=FakeMemory(results=[
             {"content": "prior claims onboard -> claims_raw"}]),
             ingestion_reader=FakeIngestionReader(preview={
                 "columns": ["Claim ID", "Amount"], "rows": [["CLM-1", "1250.50"]]}),
             obo_token="tok"),
         {"tenant_id": TENANT, "query": "Onboard the claims CSV from S3 as a dataset",
          "connection_id": "conn-1", "source_path": "s3://bucket/claims/*.csv"},
         "proposal", "ingestion.create"),

    Case("dashboard-designer", run_dashboard_designer,
         lambda llm: GraphDeps(llm=llm, semantic_reader=FakeSemanticReader(),
             catalog_reader=FakeChartCatalog(),
             memory=FakeMemory(results=[{"content": "prior: Claims Insights dashboard"}]),
             obo_token="tok"),
         {"tenant_id": TENANT, "workspace_id": "ws-claims",
          "query": "Design a claims overview dashboard"},
         "proposal", "chart.dashboard.create"),

    Case("model-training", run_model_training,
         lambda llm: GraphDeps(llm=llm, memory=FakeMemory(results=[
             {"content": "prior xgboost fraud run hit 0.94 acc"}]),
             pipeline_reader=FakePipelineReader(),
             experiment_reader=FakeExperimentReader(runs=[
                 {"metrics": {"accuracy": 0.94}, "params": {"max_depth": "4"},
                  "status": "succeeded"}]),
             obo_token="tok"),
         {"tenant_id": TENANT, "workspace_id": "ws-1",
          "query": "Train an xgboost classifier on the claims dataset to predict fraud"},
         "proposal", "pipeline.template.create_from_algorithm"),

    Case("inference", run_inference,
         lambda llm: GraphDeps(llm=llm, memory=FakeMemory(results=[
             {"content": "prior claims scoring job -> scores v1"}]),
             experiment_reader=FakeExperimentReader(), dataset_reader=FakeDatasetReader(),
             obo_token="tok"),
         {"tenant_id": TENANT,
          "query": "Run batch inference with the production claims model on the latest dataset"},
         "proposal", "inference.submit"),

    Case("ml-engineer", run_ml_engineer,
         lambda llm: GraphDeps(llm=llm, dataset_reader=FakeDatasetReader(schema=_SCHEMA),
             pipeline_reader=FakePipelineReader(), pipeline_writer=FakePipelineWriter(),
             experiment_reader=FakeExperimentReader(models=_REGISTRY_MODELS,
                                                    model=_REGISTRY_DETAIL),
             obo_token="tok"),
         {"tenant_id": TENANT, "workspace_id": "ws-1", "dataset": "auto-claims-latest",
          "label_column": "disposition"},
         "governed", "experiment.model.promote"),

    Case("meta-router", run_meta_router,
         lambda llm: GraphDeps(llm=llm, memory=FakeMemory(), obo_token="tok"),
         {"tenant_id": TENANT,
          "query": "Model drift check: drift_score 0.45, 25 corrections — should we retrain?",
          "signals": {"drift_score": 0.45, "correction_count": 25}, "model_urn": _MODEL_URN},
         "governed", "mlops.open_retrain"),
]

_ROSTER_KEYS = {c.agent_key for c in ROSTER}


def test_roster_covers_every_published_agent():
    """Guard: every PUBLISHED catalog agent (one with a real v1 graph) must have a
    real-LLM regression entry above. Fails loudly when an agent is added without
    one, so 'all agents are tested against a real model' stays true over time.
    This runs WITHOUT any live dependency (pure catalog introspection)."""
    published = {key for key, (_d, _desc, _wm, graph_ref, _sk) in CATALOG.items() if graph_ref}
    missing = published - _ROSTER_KEYS
    assert not missing, f"published agents missing a real-LLM regression: {sorted(missing)}"


@pytest.mark.parametrize("case", ROSTER, ids=[c.agent_key for c in ROSTER])
async def test_agent_reasons_through_real_ai_gateway(require_ai_gateway, case: Case):
    if not VKEY:
        pytest.skip("set AR_AI_GATEWAY_VKEY to a minted ai-gateway virtual key (nk-...)")

    llm = _real_llm()
    outcome = await case.run(case.deps(llm), dict(case.inputs))

    # (1) A REAL model was invoked through ai-gateway for THIS agent: the gateway
    #     populates usage from Ollama's response (fakes never set these).
    assert outcome.usage.get("output_tokens", 0) > 0, (
        f"{case.agent_key}: no real model tokens — did the gateway call happen?")
    assert outcome.usage.get("model"), f"{case.agent_key}: gateway returned no model id"

    # (2) The GOVERNED outcome is well-formed for this agent's contract.
    if case.mode == "read_only":
        assert outcome.write_intent is None, f"{case.agent_key} must stay read-only"
        assert (outcome.final_text or "").strip(), f"{case.agent_key} produced no answer"
    elif case.mode == "proposal":
        wi = outcome.write_intent
        assert wi is not None, f"{case.agent_key} produced no proposal"
        assert wi.tool_id == case.tool_id, (
            f"{case.agent_key} proposed {wi.tool_id!r}, expected {case.tool_id!r}")
        assert wi.tier == "write-proposal", f"{case.agent_key} is not proposal-tier"
        assert (wi.rationale or "").strip(), f"{case.agent_key} proposal has no rationale"
        assert wi.affected_urns, f"{case.agent_key} proposal names no affected URNs"
    else:  # "governed": a proposal OR an honest final answer, but never a silent nothing
        wi = outcome.write_intent
        if wi is not None:
            assert wi.tool_id == case.tool_id, (
                f"{case.agent_key} proposed {wi.tool_id!r}, expected {case.tool_id!r}")
            assert wi.tier == "write-proposal"
        else:
            assert (outcome.final_text or "").strip(), (
                f"{case.agent_key} produced neither a proposal nor an answer")

    print(f"[roster] {case.agent_key}: usage={outcome.usage} "
          f"write_intent={'yes:' + outcome.write_intent.tool_id if outcome.write_intent else 'none'} "
          f"text={(outcome.final_text or '')[:80]!r}")
