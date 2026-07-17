# Windrose Build Status

**Date:** 2026-07-12 · **Status posture:** honest, evidence-based
**Scope:** Windrose greenfield rebuild (`Windrose-ai/`) against all BRDs in `../docs/brd/` and every capability defined in the strategy doc set (see `../WINDROSE_STRATEGY.md`).

This document supersedes the stale wave table in `README.md`. It is the single truth for what's built, what's designed but not built, and what's next. It gets refreshed at the end of every material coding push.

---

## 1. Honest posture (read this first)

- **Nothing is deployed to a production customer environment.** Everything below is greenfield code with local dev infrastructure and an end-to-end verification driver. First design-partner pilot has not started.
- **All 22 Core services (BRDs 01–22) have substantial code** (4K–11K LOC each, integration tests, migrations, real dependencies). Every service has a README with a Given/When/Then AC traceability table.
- **The `insurance-claims-payer` pack (BRD 24), `pack-service` (BRD 23), and this-session BRD additions (Simple UX Charter, cost mechanisms, decision-URN cost attribution, display_labels) are DESIGN ONLY — no code.**
- **The strategic docs written this session (WINDROSE_STRATEGY, MODEL_STRATEGY, CORE_CAPABILITIES, INVESTOR_FAQ, CLAIMS_PRODUCT_SPEC) are documentation.** They do not add build capacity or reduce build backlog.
- **E2E readiness:** `deploy/e2e/driver.py` walks a real claims-triage journey across the real stack (Postgres RLS + Redpanda + OPA + Keycloak/RS256 + MinIO/Iceberg + OpenSearch + Ollama + Temporal). Passing this consistently is a first-order milestone.

## 2. Per-service capability status (evidence-based)

Evidence collected per service: production LOC · integration test files · migrations · FR checklist entries in README · presence of RUNBOOK.md · Dockerfile · genuine external-dependency wiring.

Status levels:
- **✅ Production-ready** — every FR in the BRD Done or documented-Should stub; runbook exists; all tests pass; verified in end-to-end journey.
- **🟢 Feature-complete, verification pending** — all Must-FRs implemented per README; unit + integration tests present; e2e journey uses this service; **has NOT been independently verified as production-ready** in this status pass.
- **🟡 Substantial implementation** — most Must-FRs done, known gaps flagged in README.
- **🟠 In progress** — coding under way, incomplete.
- **⚫ Design only** — BRD written, no code.

### 2.1 Foundation plane

| # | Service | Lang | Prod LOC | Tests | Migrations | Status | Notable |
|---|---|---|---|---|---|---|---|
| 01 | identity-service | Go | ~9.5K | 16 files | 5 | 🟢 | 35 FRs Done · 3 Partial (Should) · 2 out-of-scope · 14 ACs traced · RS256/RLS/OBO/JWKS all real |
| 02 | rbac-service | Go | ~10K | 13 files | 9 | 🟢 | 34 FRs Done · 2 Partial (Should) · Redis `permissions_flat` projection · dual-write authz keys · projection worker + rebuild binary |
| 18 | audit-service | Go | ~4.3K | 11 files | 7 | 🟢 | Append-only ledger · signed audit-bundle stub · cross-tenant safety verified |
| 19 | notification-service | Go | ~8.6K | 17 files | 9 | 🟢 | 19 FRs Done · digest + escalation + preferences · fanout to email/Slack/webhook stubbed at edge |
| 20 | realtime-hub | Go | ~4K | 8 files | 7 | 🟢 | SSE + WebSocket multiplexing · JWT auth · topic subscribe |

### 2.2 Data plane

| # | Service | Lang | Prod LOC | Tests | Migrations | Status | Notable |
|---|---|---|---|---|---|---|---|
| 03 | ingestion-service | Py | ~10K | 32 files | 12 | 🟢 | 35 FRs Done · chunked resumable upload · CDC · streaming ingest · **most feature-dense service** |
| 04 | dataset-service | Py | ~6K | 20 files | 6 | 🟢 | 31 FRs Done · 2 Partial · Iceberg lineage · profile pipeline hooked |
| 05 | query-service | Go | ~11K | 17 files | 7 | 🟢 | 29 FRs Done · streaming Arrow · dry-run cost estimate · Trino/DuckDB dialects · **highest LOC** |
| 06 | semantic-service | Py | ~7K | 22 files | 4 | 🟢 | 26 FRs Done · compile API + verified-query lifecycle · byte-identity contract test with chart-service |
| 07 | chart-service | Go | ~6K | 10 files | 5 | 🟢 | Dashboards + charts, semantic-service compile passthrough |

### 2.3 ML plane

| # | Service | Lang | Prod LOC | Tests | Migrations | Status | Notable |
|---|---|---|---|---|---|---|---|
| 09 | pipeline-orchestrator | Py | ~5.3K | 20 files | 4 | 🟢 | Argo Workflows execution + DAG templates + retries |
| 10 | experiment-service | Py | ~5.7K | 27 files | 6 | 🟢 | MLflow-backed run tracking + registry + promotion approval |
| 11 | inference-service | Py | ~4.9K | 17 files | 4 | 🟢 | Batch + online inference + model rollout |

### 2.4 Agentic plane

| # | Service | Lang | Prod LOC | Tests | Migrations | Status | Notable |
|---|---|---|---|---|---|---|---|
| 12 | ai-gateway | Py | ~6.7K | 19 files | 6 | 🟢 | Ollama provider adapter is real · in-process pipeline · budgets + guardrails + semantic cache all wired · **§3.8 cost FRs added this session are NOT yet in code** (see §4) |
| 13 | tool-plane | Go | ~6K | 12 files | 7 | 🟢 | MCP registry + tool-authz + audit |
| 14 | agent-runtime | Py | ~6.8K | 26 files | 12 | 🟢 | LangGraph + Temporal durable workflows + proposal framework + JWKS-signed grants + 8 seeded agent catalog (2 real graphs published: triage.v1, governance.v1) |
| 15 | memory-service | Py | ~4.8K | 15 files | 6 | 🟢 | pgvector task memory · CDC-fed RAG corpora · right-to-erasure cascades |
| 16 | eval-service | Py | ~6.3K | 14 files | 4 | 🟢 | Golden datasets + LLM-as-judge + deterministic scorers · cascade gating hook exists |

### 2.5 Governance + economics + presentation

| # | Service | Lang | Prod LOC | Tests | Migrations | Status | Notable |
|---|---|---|---|---|---|---|---|
| 08 | case-service | Go | ~6.8K | 14 files | 7 | 🟢 | Row-reference triage · state machine · SLA timers · OpenSearch projection · disposition endpoints (learning-loop entry point) |
| 17 | usage-service | Go | ~5.1K | 13 files | 7 | 🟢 | Metering ingest · rollups · budgets · rate cards · **§3.8 decision-cost FRs added this session are NOT yet in code** (see §4) |
| 21 | bff-graphql | TS | ~11K | 46 files | 0 | 🟢 | Apollo Router + 5 subgraphs · persisted queries · dataloader · **display_labels (§BFF-FR-080..088) added this session NOT yet in code** |
| 22 | ui-web | TS | ~27.6K | 199 files | 0 | 🟢 | Next.js 15 + React 19 · full module suite (data/ml/dashboards/cases/admin/copilot/inbox) · **highest LOC + test count in repo** · **Simple UX Charter operationalization (§UI-FR-060..092) added this session NOT yet in code** |

### 2.6 Cross-service infrastructure

| Component | Status | Notable |
|---|---|---|
| `deploy/docker-compose.dev.yml` | 🟢 | Postgres + Redis + Redpanda + Keycloak + Temporal + OTel + MinIO + OpenSearch + Ollama |
| `deploy/local/` runners | 🟢 | `up.sh` / `down.sh`, seed platform, spawn workers |
| `deploy/e2e/driver.py` | 🟢 | Real claims-triage journey across full stack — RS256 JWTs, real OPA, real MinIO/Iceberg, real Ollama LLM |
| `libs/go-common`, `libs/py-common` | 🟠 | Directories exist; wave-1 rule permitted per-service vendored copies. Extraction not yet done. |
| `logs/` (persisted service run logs) | 🟢 | agent.log, identity.log present — active development artifacts |

## 3. What is NOT built — evidence-based backlog

### 3.1 Design-only (BRD written this session; zero code)

These items were added to BRDs during the strategic-planning session in this thread. **None have code.** They represent post-code-writing design that must be scheduled into the next coding cycles.

| Item | Where designed | Status | Priority |
|---|---|---|---|
| **ai-gateway cost mechanisms** — deterministic-first pre-router (AIG-FR-080..081), auto-cascade (082..083), SLM tier (084), distillation candidates (085), batch tier (086..087), workflow budgets (088), decision URN attribution (089) | BRD 12 §3.8 | ⚫ | **P0** — core to cost thesis |
| **usage-service decision-linked cost & ROI** — `usage_decisions` hypertable (USG-FR-080), `decisions` fact + `value_usd` join (081), `GET /reports/decisions` (082), MCP `usage.get_decision_cost` (083), pack ROI benchmarks (084), anomaly on decision cost (085), workspace cost widget (086) | BRD 17 §3.8 | ⚫ | **P0** — pairs with AIG-FR-089 |
| **bff-graphql display_labels** — `displayLabels` root query (BFF-FR-080), `displayName` on 20+ types (081), pack-service integration contract (082), full SDL surface (083), LRU cache invalidation (084), locale fallback (085), `Viewer.displayLabels` bulk prefetch (086), isolation (087), canonical key registry (088) | BRD 21 §3 (new subsection) | ⚫ | **P1** — enables vertical vocabulary |
| **ui-web Simple UX Charter operationalization** — `CopilotHome` (UI-FR-060..062), zero-config `<CreateForm>` (063..064), Summary + Expert mode (065..066), one-primary-CTA lint (067..068), `<Label>` primitive + URN hiding (070..072), Undo toasts + typed-name gate (073..075), `<DecisionFooter>` + `<CostChip>` + `<RoiChip>` (076..078), `<CommandPalette>` (079..080), SAS release-gate suites (090..092) | BRD 22 §3 (new subsection) | ⚫ | **P1** — user-visible product differentiator |
| **master Simple UX Charter** — MASTER-FR-090..099 + SAS Acceptance Suite | 00_MASTER_BRD §2.9 (new) | ⚫ | **P1** — cross-cutting release gate |
| **pack-service** (BRD 23) — pack registry + installer + materialization contract + upgrade/rollback + marketplace + signing + `display_labels` component kind + `/packs/labels` endpoint | BRD 23 (new); labels endpoint at PKG-FR-041..044 | ⚫ | **P0** for Horizon 2; **P2** for first design partner (packs will materialize into installed services; can be manual for pilot 1) |
| **insurance-claims-payer pack (v1.0.0)** — ontology + semantic model + 3 dashboards + 3 case schemas + 8 role seeds + 3 agent recipes + 7 connectors + 6 write adapters + 3 guardrail policies + 3 golden eval sets + display_labels + distillation pipeline | BRD 24 (new) | ⚫ | **P0** — this IS the first product |

### 3.2 Existing services with known gaps (per own README)

Items each service's own README flags as "Partial", "Stub", or "Not implemented". These are the fine-grained backlog inside services that are otherwise 🟢.

**identity-service** — IDN-FR-009 version registry (stub 501), IDN-FR-011 tenant export (out of scope), IDN-FR-024 SCIM (stub 501), IDN-FR-050 Vault signer (stub adapter).
**rbac-service** — RBC-FR-007 workspace metadata icon/tags (Should, stubbed), RBC-FR-017 membership expiry (Should, expiry stored + honored in queries but Temporal timer not wired), RBC-FR-025 role templates (Could, not implemented), RBC-FR-035 public-link sharing (flag-gated Could, not implemented).
**dataset-service** — 2 Partial items per README.
**Other services** — README FR checklists show no explicit Partials; independent verification pending.

### 3.3 Cross-cutting infrastructure gaps

| Item | Description | Priority |
|---|---|---|
| **`libs/go-common` + `libs/py-common` extraction** | Wave-1 rule allowed per-service vendoring of JWT verify + error envelope + cursor + outbox + tenant middleware. Extract to shared libs. Mechanical refactor per CONVENTIONS.md. | P1 (before pack-service depends on shared display-label plumbing) |
| **Vault signer adapter for identity-service** | Currently stubbed; local RSA dev signer active. Prod requires HashiCorp Vault or equivalent. | P0 for pilot |
| **Keycloak realm-per-tenant automation** | Design present; automation coverage unverified. | P1 for pilot |
| **Cell provisioning** | Per-cell K8s provisioning (Terraform, HITRUST-friendly landing zone) — not in `Windrose-ai/deploy` yet. | P0 for pilot |
| **HITRUST + SOC 2 Type II audit posture** | Required Phase 3 gate per WINDROSE_CLAIMS_PRODUCT_SPEC.md §11. | P0 for pilot |
| **Windrose Model Radar** | Discussed in `WINDROSE_MODEL_STRATEGY.md` §6 as a first-class product feature. Not yet drafted into BRD 16 (eval-service). | P1 |
| **Contrarian defense quarterly review process** | Docs/strategy-reviews/YYYY-Qn.md ritual (WINDROSE_STRATEGY.md Appendix B). Governance mechanism, not code. First review Q3 2026. | P1 (organizational) |
| **CI lint: pack-name-in-Core grep** | Core-neutrality release gate per WINDROSE_CORE_CAPABILITIES.md §6 falsifiability test 4. | P1 |
| **SAS release-gate CI wiring** | Simple UX Charter Simplicity Acceptance Suite as P0 release blocker. | P1 (after ui-web SAS tests land) |

### 3.4 Design-partner pilot readiness (P0 items only, in critical-path order)

To onboard a first design-partner payer, these are the load-bearing gaps:

1. **BRD 24 pack manifest built as YAML + component files** — even a hand-installed version (before pack-service is real) makes the pilot possible. Estimate: 6–10 engineer-weeks.
2. **ai-gateway cost mechanisms (AIG-FR-080..089)** — required for the cost thesis to hold in demos. Estimate: 4–6 engineer-weeks.
3. **usage-service decision-linked cost (USG-FR-080..083)** — required for cost-per-decision panel. Estimate: 3–4 engineer-weeks.
4. **First insurance connector: `facets_v6` read** — depends on Cognizant Facets SDK access from the design partner. Estimate: 4–6 engineer-weeks.
5. **PA agent recipe (INS-FR-030..035) — real graph in agent-runtime** — likely reuses triage.v1 as scaffold. Estimate: 3–4 engineer-weeks.
6. **HIPAA guardrail (INS-FR-080) + PHI masking end-to-end validated** — release gate. Estimate: 2–3 engineer-weeks + independent audit.
7. **Cell provisioning + HITRUST posture** — parallel workstream, longer lead time. Estimate: 8–12 weeks.
8. **`/admin` audit-bundle export (INS-FR-102) MVP** — CCO-facing evidence kit. Estimate: 2–3 engineer-weeks.
9. **SAS journey B and C (case resolution + NL chart) passing on pack-installed workspace** — release gate. Estimate: 2–3 engineer-weeks after ui-web display_labels + Simple UX operationalization land.

Aggregate: **~35–50 engineer-weeks to first pilot readiness** assuming 2–3 engineers per workstream. Corresponds roughly to a **90–120-day team push** if 6–8 engineers are focused.

## 4. Explicit "designed this session, not yet in code" backlog

For traceability against the session that produced these designs:

| BRD section | FRs | Words in BRD | Est. build weeks | Notes |
|---|---|---|---|---|
| AIG cost-reduction routing | AIG-FR-080..092 | ~2,400 | 4–6 | Deterministic-first + cascade + SLM tier + distillation + batch + decision URN |
| USG decision-linked cost | USG-FR-080..086 | ~1,200 | 3–4 | `usage_decisions` hypertable + reports + MCP tool + pack benchmarks |
| BFF display_labels | BFF-FR-080..088 | ~1,500 | 2–3 | SDL surface + pack-service contract + LRU + registry |
| Master Simple UX Charter | MASTER-FR-090..099 + SAS | ~1,800 | (cross-cutting — enforcement across BRD 22 + 21 + others) |
| UI Simple UX operationalization | UI-FR-060..092 | ~3,500 | 5–7 | CopilotHome + primitives + SAS suites |
| Pack-service | 60+ FRs (PKG-FR-001..088) | ~8,000 | 8–12 | Registry + installer + materialization + upgrade + marketplace + signing |
| Insurance-claims-payer pack | 110+ FRs (INS-FR-001..110) | ~9,000 | 20–30 | Includes agent recipes, connectors, guardrails, evals, dashboards, docs |

**Total design-only additions:** ~27,000 words of BRD content, ~40–60 engineer-weeks of implementation.

## 5. Wave/priority reordering (supersedes stale README table)

Original README wave plan (identity+rbac+ingestion+dataset as wave 1, everything else pending) is **outdated**. Actual state is that **all 22 Core services have substantial implementation**. The corrected roadmap is:

### Phase 1 — Verify what's built (weeks 1–4)
- Run full test suite per service; document pass/fail.
- Run `deploy/e2e/driver.py` end-to-end; capture the exact FRs it exercises.
- Publish a per-service certification report ("production-ready" vs "verification pending").
- Extract `libs/go-common` + `libs/py-common` (mechanical refactor).
- Wire the pack-name-in-Core CI grep.

### Phase 2 — Ship the cost thesis (weeks 5–14)
- Implement AIG-FR-080..092 (cost mechanisms).
- Implement USG-FR-080..086 (decision-linked cost + ROI).
- Implement BFF-FR-080..088 (display_labels).
- Add the frontend `<DecisionFooter>` + `<CostChip>` + `<RoiChip>`.

### Phase 3 — Simple UX release gate (weeks 15–20)
- Implement UI-FR-060..092 (chat-first, one-CTA, undo, command palette).
- Ship SAS Acceptance Suite as P0 release blocker.
- Wire `<Label>` primitive throughout ui-web.

### Phase 4 — First vertical (weeks 21–34)
- Build BRD 23 pack-service (or ship pack v1 hand-installed for pilot 1; retrofit into pack-service later).
- Build BRD 24 pack manifest + agent recipes + connectors + guardrails.
- HIPAA + HITRUST + SOC 2 audit posture landed.
- First design-partner pilot in shadow mode.

### Phase 5 — Design-partner production (weeks 35–52)
- Shadow → proposal-mode promotion (per gates).
- Model Radar first quarterly report.
- Case study drafted + jointly published.

## 6. Refresh policy

- Refresh this doc after every material coding push.
- Refresh at end of each phase above (verified checklist per service).
- Refresh when new FRs are added to any BRD (append to §3.1 with priority).
- Cross-reference the quarterly contrarian-defense review (`WINDROSE_STRATEGY.md` Appendix B) — drift risk that shows up in that review may reprioritize this backlog.
