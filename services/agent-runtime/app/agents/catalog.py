"""Agent catalog seed (ART-FR-040): the 8 agent definitions, with real published
v1 graphs for the priority agents (case-triage, governance, analytics). Adding an
agent is a definition + graph module — no runtime fork.
"""

from __future__ import annotations

from app.domain.entities import AgentDefinition, AgentVersion
from app.graphs.base import graph_digest
from app.signing import build_card, sign_card

# key -> (display, description, write_mode, graph_ref|None, skills)
CATALOG = {
    "case-triage": ("Case Triage Copilot",
                    "Proposes claim dispositions (severity/assignee/disposition) grounded in "
                    "case data + resolved-case RAG.", "proposal", "triage.v1",
                    [{"id": "triage_claim", "description": "Propose a disposition for a claim case",
                      "tags": ["claims", "triage", "proposals"]}]),
    "governance": ("Governance Agent",
                   "Opens retrain proposals when drift/correction signals exceed thresholds.",
                   "proposal", "governance.v1",
                   [{"id": "open_retrain", "description": "Propose a model retrain",
                     "tags": ["mlops", "governance", "proposals"]}]),
    "analytics": ("Analytics Agent",
                  "Conversational analytics over governed semantic-layer data. Read-only.",
                  "read_only", "analytics.v1",
                  [{"id": "answer_data_question", "description": "Answer NL data questions",
                    "tags": ["analytics", "read-only"]}]),
    "onboarding": ("Onboarding Agent",
                   "Proposes ingestion configs and column mappings grounded in the "
                   "connector catalog + source schema preview + prior-onboarding RAG.",
                   "proposal", "onboarding.v1",
                   [{"id": "onboard_source",
                     "description": "Propose an ingestion config + column mapping for a source",
                     "tags": ["ingestion", "onboarding", "proposals"]}]),
    "dashboard-designer": ("Dashboard Designer",
                           "Proposes draft dashboards (a title + charts) grounded in the governed "
                           "semantic layer (measures/dimensions) + the chart-type catalog.",
                           "proposal", "dashboard_designer.v1",
                           [{"id": "design_dashboard",
                             "description": "Propose a draft dashboard with charts over "
                                            "the semantic layer",
                             "tags": ["insights", "dashboards", "proposals"]}]),
    "model-training": ("Model Training Agent",
                       "Proposes governed training runs: fills a pipeline template "
                       "(algorithm, hyperparameters, label/feature columns) grounded in the "
                       "algorithm-template schema + prior experiment history.",
                       "proposal", "model_training.v1",
                       [{"id": "train_model",
                         "description": "Propose a training run for an algorithm on a dataset",
                         "tags": ["mlops", "training", "proposals"]}]),
    "inference": ("Inference Agent",
                  "Proposes batch inference jobs grounded in the registered model's "
                  "production version + input-dataset schema compatibility.",
                  "proposal", "inference.v1",
                  [{"id": "run_inference",
                    "description": "Propose a batch inference job",
                    "tags": ["inference", "mlops", "proposals"]}]),
    "meta-router": ("Meta Router",
                    "Classifies a free-text request and delegates to the specialist "
                    "agent whose skill matches (analytics/onboarding/model-training/"
                    "inference/dashboard-designer/governance); the delegate's own "
                    "write mode governs whether a proposal results.",
                    "proposal", "meta_router.v1",
                    [{"id": "route_request",
                      "description": "Classify and delegate a request to the "
                                     "matching specialist agent",
                      "tags": ["routing", "meta", "delegation"]}]),
}


async def seed_catalog(store, signing_key, *,
                       endpoint_base: str = "https://agent-runtime.internal") -> None:
    for key, (display, desc, wmode, graph_ref, skills) in CATALOG.items():
        await store.upsert_agent_definition(AgentDefinition(
            agent_key=key, display_name=display, description=desc, owner_team="platform-ai",
            default_write_mode=wmode,
            status="published" if graph_ref else "draft"))
        if graph_ref is None:
            continue
        if await store.get_agent_version(key, 1) is not None:
            continue
        card = build_card(agent_key=key, version=1, display_name=display, description=desc,
                          write_mode=wmode, skills=skills,
                          endpoint=f"{endpoint_base}/a2a/{key}")
        signature = sign_card(signing_key, card)
        card["signature"] = {"alg": "RS256", "kid": signing_key.kid, "value": signature}
        toolset = ([{"tool_id": "case.apply_disposition", "version_range": ">=1.0.0"}]
                   if key == "case-triage" else
                   [{"tool_id": "mlops.open_retrain", "version_range": ">=1.0.0"}]
                   if key == "governance" else
                   [{"tool_id": "ingestion.create", "version_range": ">=1.0.0"}]
                   if key == "onboarding" else
                   [{"tool_id": "chart.dashboard.create", "version_range": ">=1.0.0"}]
                   if key == "dashboard-designer" else
                   [{"tool_id": "pipeline.template.create_from_algorithm",
                     "version_range": ">=1.0.0"}]
                   if key == "model-training" else
                   [{"tool_id": "inference.submit", "version_range": ">=1.0.0"}]
                   if key == "inference" else
                   # meta-router forwards whichever delegate produced the write
                   # intent (§8.4); it needs the union of delegate write tools so
                   # its own agent_version registration stays a truthful superset.
                   [{"tool_id": "ingestion.create", "version_range": ">=1.0.0"},
                    {"tool_id": "chart.dashboard.create", "version_range": ">=1.0.0"},
                    {"tool_id": "pipeline.template.create_from_algorithm",
                     "version_range": ">=1.0.0"},
                    {"tool_id": "inference.submit", "version_range": ">=1.0.0"},
                    {"tool_id": "mlops.open_retrain", "version_range": ">=1.0.0"}]
                   if key == "meta-router" else [])
        await store.create_agent_version(AgentVersion(
            agent_key=key, version=1, graph_ref=graph_ref, graph_digest=graph_digest(graph_ref),
            prompt_refs=[{"id": f"{key}-sys", "digest": "seed"}], toolset=toolset,
            model_config={"request_class": "chat", "max_rung": 1, "temperature": 0.2},
            memory_policy={"scopes_readable": ["workspace", "tenant"], "scopes_writable": []},
            eval_gate={"suite_id": f"{key}-suite", "baseline_version": 0,
                       "thresholds": {"min_score": 0.6}},
            eval_gate_result_id="seed-gate-pass",
            a2a_card=card, card_signature=signature,
            principal_ref=f"spiffe://windrose/ns/ai/agent/{key}", status="published"))
