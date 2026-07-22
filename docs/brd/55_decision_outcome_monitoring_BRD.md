# BRD 55 — Decision Outcome Monitoring

**Deliverable type:** Core capability (case-service + eval-service + chart-service + agent-runtime)
**Publisher:** Datacern · **Initial version:** 1.0.0 · **Status:** authored; increment 1 BUILT + live-verified (human-labeled outcomes + effectiveness); SoR inbound capture, drift review, and SFT enrichment remain unbuilt
**Closes:** the partial Decision-Monitoring capability. See `docs/design/di-completeness-roadmap.md`.

---

## 1. Overview

**Purpose.** Move monitoring from "model metrics + captured corrections" to the
category's real bar: *did decisions of this TYPE produce good OUTCOMES over
time, and is the decision logic drifting?* Join realized outcomes back to the
decisions that produced them, surface decision-effectiveness KPIs, detect drift
on the DECISION (not just the model), and feed that signal into the learning
loop and BRD 54 decision models.

**Why.** Datacern captures corrections (M1/M2) and model metrics, but the
article's Decision-Monitoring capability is specifically about *outcome*
tracking — the feedback loop that "separates a true DI platform from a static
rules engine." Today a resolved case records its disposition; it does not record
whether that disposition turned out RIGHT (the chargeback was ultimately won,
the SAR was substantiated, the promotion actually improved production metrics).
Without that, "continuous improvement" is asserted, not measured.

**The mechanism.** A decision (disposition/proposal/model-table outcome) gets an
**outcome label** later — from the tenant's system of record via the existing
write-back/connection rails, from a human marking it, or from a downstream event
— joined on the decision's provenance. Effectiveness = agreement between
predicted/decided and realized outcome, sliced by decision type, model version,
agent/table, persona, and time. Drift = a shift in that effectiveness or in the
input distribution for a decision type.

**In scope.** Outcome-label capture + join to decisions; decision-effectiveness
KPIs in the semantic layer + dashboards; decision drift signals; a feedback hook
that (a) enriches the SFT corpus with outcome-labeled examples and (b) can flag a
BRD 54 decision model or an agent for review. **Out of scope:** automated model
retirement (proposes review, never auto-retires); causal attribution ("did the
decision CAUSE the outcome" — correlational effectiveness only).

## 2. Actors & user stories

Decision-ops Manager (MA), Compliance Auditor (CA), Data/Model Owner (MO),
Tenant Admin (TA).

- **US-1** As an MA, I see, per decision type, the effectiveness rate (decided
  vs realized outcome) trending over time — not just how many decisions we made.
- **US-2** As an MO, when a model version's decisions start disagreeing with
  realized outcomes, I get a drift flag proposing a review (never an auto-change).
- **US-3** As a CA, I can show a regulator that decisions are monitored for
  real-world effectiveness, with the evidence chain.
- **US-4** As the platform, outcome-labeled decisions enrich the SFT corpus so
  the learning loop trains on what was ultimately RIGHT, not just what a human
  corrected in the moment.
- **US-5** As an MA, I can compare effectiveness across agents / decision tables
  / personas for the same decision type (which producer decides best).

## 3. Functional requirements (summary)

- **OM-FR-001 — Outcome label model:** `{decision_ref (proposal/case urn), decision_type, decided_outcome, realized_outcome, label_source (sor|human|event), labeled_at}`; tenant-scoped, joined to the decision's provenance.
- **OM-FR-010 — Capture paths:** (a) inbound from the tenant SoR via existing connections/write-back reconciliation; (b) human mark-outcome on a resolved case; (c) event ingestion. All governed, audited.
- **OM-FR-020 — Effectiveness semantic model:** measures — effectiveness_rate, reversal_rate, time-to-outcome, effectiveness by model_version/agent/table/persona/type/month — over the governed layer, charted like any KPI.
- **OM-FR-030 — Decision drift:** detect a material drop in effectiveness or input-distribution shift for a decision type; raise a proposal-mode REVIEW (never an auto-retire) via the governance agent.
- **OM-FR-040 — Learning-loop enrichment:** outcome labels attach to transcripts so SFT curation can weight/label by realized correctness (extends BRD 12 M2).
- **OM-FR-050 — Governance:** every label + drift flag audited; RLS; no cross-tenant leakage; labels never silently alter a closed decision (they annotate, not mutate).

## 4. Business rules
- **BR-1** Outcome labels ANNOTATE decisions; they never mutate a closed
  decision or auto-change logic.
- **BR-2** Drift → proposal-mode review, human-approved (no autonomous
  retirement).
- **BR-3** Effectiveness is correlational, labeled as such — not a causal claim.
- **BR-4** Tenant-scoped, audited, RLS — same spine.
- **BR-5** Outcome capture from the SoR reuses the governed connection/write-back
  rails; no new credential surface.

## 5. Acceptance criteria
- **AC-1** A resolved decision receives an outcome label (human + SoR paths) joined on provenance. **(inc1: human path only — SoR/event capture paths (a)/(c) of OM-FR-010 not yet built)**
- **AC-2** An effectiveness dashboard trends decided-vs-realized by decision type + model version over time. **(inc1: `GET /decision-effectiveness?by=` API sliced by decision_type/producer, live-verified — no semantic-model/dashboard surfacing yet)**
- **AC-3** A drop in effectiveness raises a proposal-mode review, not an auto-change. **(not built — OM-FR-030 drift detection remains future work)**
- **AC-4** Outcome-labeled examples appear in the SFT curation surface. **(not built — OM-FR-040 remains future work)**
- **AC-5** Full audit + tenant isolation; labels annotate, never mutate. **(inc1, live-verified)**

## 5a. Increment 1 — what shipped (task #21)
BUILT + live-verified in wr-disputes. Attaches a REALIZED outcome to any decision (a
`proposals` row from an agent, decision-table, or persona copilot), joined on the
proposal's provenance so effectiveness needs no extra input; reads decided-vs-realized
agreement sliced by `decision_type` or `producer`. Correlational only (BR-3); labels
annotate, never mutate the closed proposal (BR-1); one label per decision (upsert).
**Code (agent-runtime):** migration `0011_outcome_labels.py`
(`UNIQUE(tenant_id, decision_ref)`); `app/domain/outcomes.py` (`OutcomeLabel`,
case/space-insensitive `compute_correct`, `effectiveness(labels, by=)`); store methods in
`sql.py`/`memory.py`; `app/api/routes/outcomes.py` — `POST/GET /decisions/{ref}/outcome`,
`GET /decision-effectiveness?by=decision_type|producer`. Tests:
`tests/unit/test_outcome_monitoring.py` (7, green), zero regressions. Live-verified:
labeled a decision-table proposal (decided severity=high, realized=low → correct=false)
and a custom-agent proposal (high→high → correct=true); `/decision-effectiveness`
correctly split `by_type` (rate 0.5) and `by_producer` (decision-model 0.0 vs
custom-agent-copilot 1.0), with both underlying proposals left `status=approved`
(annotate-not-mutate confirmed). **Deferred to a later increment:** SoR/event inbound
capture (OM-FR-010 b/c), the effectiveness semantic model + chart-service dashboard
(OM-FR-020), decision-drift review (OM-FR-030), and SFT-corpus enrichment (OM-FR-040).

## 6. Dependencies
case-service (decisions/dispositions), ingestion-service (SoR connections +
write-back reconciliation), eval-service + agent-runtime (SFT corpus, governance
agent), semantic + chart services (effectiveness KPIs), audit.

## 7. Out of scope / future
Causal attribution; automated retirement; real-time outcome streaming (batch/
reconciliation first).
