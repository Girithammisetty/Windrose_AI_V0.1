# BRD 56 — Entity Resolution (Data-Unification for Decisions)

**Deliverable type:** Core capability (new entity-resolution component + dataset/semantic integration)
**Publisher:** Windrose · **Initial version:** 1.0.0 · **Status:** authored (DESIGNED; sequenced LAST; build-vs-buy open)
**Closes:** the data-unification / entity-resolution gap. See `docs/design/di-completeness-roadmap.md`.

---

## 1. Overview

**Purpose.** Build unified views of a real-world entity — customer, counterparty,
supplier, patient, cardholder — across fragmented records that use different
identifiers, before decisions run on them. The category places entity resolution
at stage 1 (data unification) and calls it "particularly valuable" in banking/
AML, where the same entity appears across systems under different identifiers.

**Why.** Windrose decisions today run on governed datasets as ingested — a
dispute is scored on its own row, a claim on its own record. But the highest-
value regulated decisions (AML alert triage, fraud, KYC, credit) depend on
seeing that "Viktor Petrov" in the wire feed, "V. A. Petrov" in the KYC book,
and "Petrov Holdings" in the ownership graph are ONE resolved entity. Without
entity resolution, decisions are made on incomplete or conflicting pictures —
the exact failure the category names. This is also precisely the differentiation
Quantexa is known for; closing it neutralizes a competitor advantage in
financial-services deals.

**Honest framing (build vs buy).** This is the LARGEST lift of the DI-
completeness gaps and the one where a commercial ER/graph component is a
legitimate alternative to building. The BRD specifies the CAPABILITY and its
integration contract so Windrose can either build a first-party resolver or wrap
a component behind the same governed surface — the platform's value is the
governed decisioning ON TOP of resolved entities, not the resolver internals.

**In scope (capability).** An entity-resolution service/component that ingests
record sets, produces resolved entity clusters (deterministic + probabilistic
matching on identifiers/attributes), exposes a resolved-entity view as a
governed dataset/semantic entity that packs and decision models can read, and
maintains cluster lineage (which records merged, why) for audit. **Out of scope
(v1):** real-time streaming resolution (batch first); a full graph-analytics
engine (community detection/PageRank — the pack blueprint's separate future
Core enhancement); automated merge of records into the SoR (proposes links,
never mutates the source).

## 2. Actors & user stories

Data Steward (DS), AML/Fraud Investigator (IN), Compliance Auditor (CA),
Decision Author (DA), Tenant Admin (TA).

- **US-1** As an IN, an AML alert shows the RESOLVED party — every account,
  wire, and ownership link across systems — not just the one flagged row.
- **US-2** As a DS, I configure which fields resolve entities (name+DOB+ID for
  persons; registration+address for orgs) and review probable-match clusters
  before they're trusted.
- **US-3** As a DA, my decision table / pack can read resolved-entity attributes
  (e.g. total_exposure_across_accounts) as governed columns.
- **US-4** As a CA, every resolved cluster has lineage: which records merged, on
  what evidence, at what confidence — reconstructable and defensible.
- **US-5** As a TA, resolution runs within tenant RLS; no entity crosses tenants;
  probable matches are proposed, not silently merged.

## 3. Functional requirements (summary)

- **ER-FR-001 — Resolution config:** per-entity-type match rules (deterministic keys + probabilistic scoring on identifier/attribute similarity), tenant-scoped, versioned.
- **ER-FR-010 — Cluster production:** batch resolution over configured record sets → entity clusters with a stable resolved_entity_id + member records + match confidence.
- **ER-FR-020 — Governed resolved-entity view:** clusters exposed as a governed dataset + semantic entity (RLS), readable by packs, decision models (BRD 54), agents, and dashboards.
- **ER-FR-030 — Human-in-the-loop merges:** below-threshold / ambiguous matches are PROPOSED for a steward's four-eyes review (reuse the proposal spine), never auto-merged.
- **ER-FR-040 — Lineage + audit:** every cluster records its member records, matching evidence, confidence, and version; fully reconstructable.
- **ER-FR-050 — No SoR mutation:** resolution produces a link/view layer; it never writes merged records back to the source (a write-back to the SoR, if wanted, is a separate governed proposal).

## 4. Business rules
- **BR-1** Probable matches are PROPOSED (four-eyes), never silently merged;
  deterministic exact-key matches may auto-cluster with lineage.
- **BR-2** Resolution respects tenant RLS — no entity spans tenants.
- **BR-3** Every cluster is lineage-complete + audited (defensible under exam).
- **BR-4** Resolution never mutates the SoR; it is a governed view/link layer.
- **BR-5** Config is versioned; re-resolution under a new config is explicit and
  auditable.

## 5. Acceptance criteria (for the eventual build)
- **AC-1** Configured resolution over a person record set produces clusters with confidence + lineage.
- **AC-2** A resolved-entity attribute is readable as a governed column by a decision model / pack.
- **AC-3** An ambiguous match is proposed for steward four-eyes review, not auto-merged.
- **AC-4** Cluster lineage reconstructs which records merged, on what evidence.
- **AC-5** Tenant isolation + audit; no SoR mutation.

## 6. Dependencies
dataset-service + semantic-service (resolved-entity view), agent-runtime
(proposal spine for HITL merges), a resolution component (build or wrapped),
audit, rbac. Interplay with the AML pack (BRD 30) and the network-analytics
future Core enhancement noted in the pack blueprint.

## 7. Out of scope / future
Real-time streaming resolution; full graph analytics (community detection/
PageRank — separate Core enhancement); autonomous SoR merges; cross-tenant
entity graphs.
