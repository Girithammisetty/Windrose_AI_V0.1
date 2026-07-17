# BRD 52 — Autonomous ML-Engineer Agent (train → evaluate → propose)

**Deliverable type:** Core capability (agent-runtime + tool-plane + registrations) · **Publisher:** Windrose
**Initial version:** 1.0.0 · **Status:** authored, build in progress
**Relationship to other BRDs:** extends BRD 14 (agent-runtime) and BRD 13 (tool-plane); consumes BRD 09 (pipeline-orchestrator), BRD 10 (experiment-service), BRD 16 (eval-service), BRD 04 (dataset-service). NOT a pack — packs may configure this agent via TenantAgentConfig like any other fixed agent (BRD 23 invariant: packs never mint agents).

---

## 1. Overview

**Purpose.** A ninth fixed platform agent, `ml-engineer`, that autonomously performs the
data-scientist grunt work — inspect a governed dataset, configure and launch training
pipelines, track experiments, evaluate candidates, compare results — and brings the one
decision that matters to a human: **"promote this model," proposed with evidence, approved
four-eyes.** Autonomy for the labor, governance for the judgment.

**Why.** Enterprises want agents that work against their real data end-to-end, but no
regulated buyer will grant an agent raw database credentials or unsupervised model
promotion. Windrose's architecture already splits this correctly: connections are
platform objects (credentials in Vault, never in the agent), every agent action is a
tool-plane-governed, RBAC-checked, audited platform API call, and promotion already has
a four-eyes gate. This BRD composes existing rails into the autonomous loop — zero new
security surface, zero pack-invariant changes.

**The workflow being automated (detail analysis).** Today a human data scientist on the
platform performs, in order: (1) pick a dataset and read its schema/profile; (2) choose
target column + feature columns; (3) pick an algorithm from the catalog; (4) configure a
pipeline (prep components + training spec); (5) launch and babysit the run; (6) read
metrics from the experiment run; (7) repeat 3–6 for candidate algorithms; (8) compare
candidates; (9) request promotion of the winner; (10) a second person approves the
promotion. Steps 1–8 are mechanical, reversible, and sandboxed (they create runs and
artifacts, touch nothing downstream) — they are safe to automate. Steps 9–10 change what
the platform serves for inference — they stay human. The agent automates 1–8, executes 9
as a **proposal**, and never performs 10.

**In scope.** Tool-plane registrations for the ML lifecycle (dataset inspect, algorithm
catalog, pipeline launch, run status/metrics, model promotion **proposal**); the
`ml-engineer` LangGraph agent (plan → train candidates → evaluate → compare → propose);
risk-tiered autonomy policy; transcript capture into the M1/M2 learning corpus; budget
enforcement via ai-gateway; UI surfacing through the existing proposal inbox and agent
session views.

**Out of scope.** Autonomous model promotion or deployment (never); direct database
access by the agent (connections stay admin-created platform objects); new ML algorithms
(uses the existing 21-template catalog); feature-store work; AutoML hyperparameter sweeps
beyond candidate-per-algorithm (future iteration); agent-initiated ingestion of NEW
external sources (Phase 2 — see §9).

## 2. Actors & user stories

**Personas:** Data Scientist / Builder (DS), Decision-ops Manager / model approver (MA),
Compliance Auditor (CA), Tenant Admin (TA), the ml-engineer agent (AGENT).

- **US-1** As a DS, I point the agent at a governed dataset and a target column and say
  "build me a scorer"; the agent inspects the schema, trains candidate models, evaluates
  them, and returns a comparison — without me configuring a single pipeline by hand.
- **US-2** As a DS, I can watch every step the agent took (tool calls, runs launched,
  metrics read) in the agent session view, and every artifact it created is a normal
  platform object (pipeline run, experiment run, registered model version) I can open.
- **US-3** As an MA, when the agent believes a candidate is promotion-worthy I receive a
  **proposal** in my approval inbox carrying the evidence: candidate metrics, comparison
  against alternatives, dataset version, and the agent's rationale. I approve or reject;
  nothing is promoted until I act.
- **US-4** As an MA, an agent proposal to promote is blocked from self-approval (four-eyes
  is enforced by the platform, not the agent's good manners).
- **US-5** As a CA, I can reconstruct the full chain for any promoted model: who asked the
  agent, every tool call it made, the runs and metrics, the proposal, and the named human
  approver — from the audit log and transcripts.
- **US-6** As a TA, I can enable/disable the ml-engineer agent per tenant, cap its LLM
  spend via ai-gateway budgets, and kill-switch it instantly.
- **US-7** As a DS, the agent refuses gracefully when the dataset is unsuitable (no rows,
  no numeric features, missing target) and says why, instead of hallucinating a result.
- **US-8** As the platform owner, every agent run lands in the transcript corpus (M1), so
  corrections to its proposals become training data (M2) — the agent itself is inside the
  learning loop.

## 3. Functional requirements

### MLE-FR-001 — Tool registrations (tool-plane)
Register the ML-lifecycle tools with schemas, versions, and risk tiers:

| Tool | Backs onto | Risk tier | Autonomy |
|---|---|---|---|
| `dataset.inspect` | dataset-service get/profile/rows-sample | low | auto |
| `pipeline.algorithms` | pipeline-orchestrator template catalog | low | auto |
| `pipeline.train` | create+launch training pipeline run | medium | auto (sandboxed, budget-capped) |
| `pipeline.run_status` | run status + metrics + model refs | low | auto |
| `experiment.compare` | experiment-service run/model reads | low | auto |
| `model.propose_promotion` | experiment-service promotion request | **high** | **proposal only — never auto** |

All tools execute through tool-plane enforcement (allowlist, tenant enablement,
kill-switch, audit) with the agent's OBO token; RBAC actions are the same ones a human
would need (no privileged agent bypass).

### MLE-FR-010 — The ml-engineer agent graph (agent-runtime)
LangGraph recipe: `intake → inspect_dataset → plan_candidates → (train → poll → collect)×N
→ evaluate_and_compare → recommend → propose_or_report`. Deterministic-first: schema
inspection and candidate selection use typed logic with LLM assistance for target/feature
reasoning; every LLM output that drives a tool call is validated against the tool schema.
The graph caps candidates (default 3), caps polling time, and fails closed with a
human-readable report on any step error.

### MLE-FR-020 — Risk-tiered autonomy policy
Autonomy is a property of the TOOL, not the agent's judgment: low/medium-tier tools
execute directly (they create only sandboxed, reversible artifacts); the high-tier
promotion tool ALWAYS materializes as a proposal (existing ai.proposal machinery,
risk_tier=high) routed to the approval inbox. Approving executes the promotion request
through the existing four-eyes experiment-service path — the approver of the proposal and
the promotion approver remain distinct where policy requires.

### MLE-FR-030 — Evidence-carrying proposals
The promotion proposal's rationale MUST include: dataset name+version, target column,
candidates trained with per-candidate metrics, the selected candidate and why, and the
registered model name/version to promote. No metrics, no proposal (the agent cannot
propose an unevaluated model).

### MLE-FR-040 — Learning-loop capture
Every ml-engineer run writes a transcript (M1); approve/reject/correct decisions on its
proposals flow into SFT curation (M2) exactly like triage corrections.

### MLE-FR-050 — Budgets & kill switches
Agent LLM calls ride ai-gateway (existing per-scope budgets). Tool-plane kill switches
and per-tenant tool enablement apply. A disabled tenant sees the agent absent, not erroring.

### MLE-FR-060 — Tenant configuration
`ml-engineer` ships disabled-by-default; TenantAgentConfig enables it and may set
prompt_params (persona, candidate cap, allowed algorithm families) — the same
Core-neutral mechanism packs already use for triage/analytics.

## 4. Business rules

- **BR-1** No autonomous promotion, deployment, or write to any system of record — the
  high-tier tool can only create a proposal. Attempted direct promotion by the agent is a
  platform authz failure, not a prompt-discipline hope.
- **BR-2** The agent holds no credentials: all data access is via governed datasets;
  connections remain admin-created; Vault secrets never transit the agent.
- **BR-3** Four-eyes integrity: the requesting user cannot approve the agent's promotion
  proposal on their own work where existing promotion policy forbids self-approval.
- **BR-4** Deterministic guardrails over prompt guardrails: tool schemas validate every
  argument; candidate/poll/budget caps are code, not instructions.
- **BR-5** Honest failure: unsuitable data or failed runs produce a clear report — the
  agent never fabricates metrics (metrics are read from experiment-service, never
  generated by the LLM).
- **BR-6** Every step audited: tool-plane + service audit logs + transcript give a
  reconstructable chain per US-5.

## 5. NFRs (deltas)

| Metric | Target |
|---|---|
| End-to-end loop (3 candidates, seed-scale data) | ≤ 10 min wall clock |
| Agent LLM spend per loop (local Ollama / API) | budget-capped, visible in usage |
| Proposal evidence completeness | 100% (schema-enforced) |
| Autonomous writes outside sandbox | 0, by construction |

## 6. Acceptance criteria

- **AC-1** Tools registered + tenant-enabled; visible in Admin → Tools; kill switch works.
- **AC-2** In a live tenant, the agent takes a dataset + target column and produces ≥2
  trained candidates with real metrics read from experiment-service.
- **AC-3** The agent emits a promotion proposal carrying full evidence (MLE-FR-030) into
  the approval inbox; approving it executes the promotion path; rejecting leaves the
  registry untouched.
- **AC-4** The agent cannot promote directly (authz-verified negative test).
- **AC-5** Full-chain audit reconstruction (US-5) demonstrated on the e2e run.
- **AC-6** Transcript captured for the run; decision on the proposal lands in the corpus.
- **AC-7** Unsuitable-dataset run ends in an honest failure report (US-7).
- **AC-8** All work is Core: zero pack changes; packs can enable/configure the agent via
  TenantAgentConfig only.

## 7. Dependencies

agent-runtime (graph registration, proposals, transcripts), tool-plane + tool-registry
(registrations, enforcement), pipeline-orchestrator (21-algo catalog, training runs),
experiment-service (runs/registry/promotions), dataset-service (schema/profile),
ai-gateway (LLM + budgets), rbac (actions), ui-web (existing inbox/session surfaces).

## 8. Rollout

Phase 1 (this BRD): the loop on already-governed datasets, one tenant e2e-verified.
Phase 2: agent-initiated ingestion from EXISTING connections (still admin-credentialed) —
extends autonomy one step left with the same tiering. Phase 3: scheduled retrain loops
(WS3) driven by drift signals, closing into the SLM/retrain roadmap (BRD 12 M3–M5).

## 9. Out of scope / future

Hyperparameter sweeps/AutoML search; agent-created connections (never — admin-only);
unstructured-evidence features (separate Core gap); cross-tenant learning (forbidden);
autonomous deployment (never).
