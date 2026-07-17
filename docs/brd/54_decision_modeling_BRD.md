# BRD 54 — Decision Modeling (Governed Decision Tables)

**Deliverable type:** Core capability (agent-runtime + rbac + ui-web)
**Publisher:** Windrose · **Initial version:** 1.0.0 · **Status:** authored; increment 1 BUILT + live-verified
**Closes:** the one Gartner DI capability marked incomplete (Decision Modeling). See `docs/design/di-completeness-roadmap.md`.
**Reuses:** BRD 53 guardrail envelope + BRD 14 proposal/four-eyes machinery (a decision table executes to the SAME governed proposal).

---

## 1. Overview

**Purpose.** Give business users an explicit, versioned, testable place to author
decision logic — a **decision table**: ordered `when (conditions) → then
(outcome)` rules over a dataset's real columns — that EXECUTES to a governed,
four-eyes proposal. Deterministic, explainable (the fired rule is named), no
code, no LLM, no logic "buried in a spreadsheet or one analyst's head" (the
category's exact framing).

**Why.** Windrose's decision logic today lives in pack dispositions, agent
prompts, and ML models — powerful, but not the visual/config, editable decision
model Gartner's Decision-Modeling capability requires. It is the single
capability where a category evaluation marks Windrose incomplete, and it is the
"rules" leg of the three DI modes (support / augment / **automate**) that the
platform was thinnest on. A decision table is the DI-native version of a
decision-management-system rule set, but governed by the same four-eyes +
guardrail + audit spine as everything else.

**The composition insight.** A decision table is a NON-LLM decision producer: it
evaluates deterministically and emits the same `WriteIntent` an agent would, so
it flows through `ProposalService` unchanged — inheriting four-eyes, the
caller-gate, the guardrail tool-allowlist, audit, and the learning corpus for
free. Zero new governance surface; the decision model just becomes another
governed source of proposals.

**In scope (inc 1).** A tenant-scoped, versioned decision-model artifact
(condition→outcome rules over real columns); a deterministic evaluator
(first-match, typed operators, default rule, explainability); a governed
`evaluate → proposal` path reusing ProposalService; create/get/list/evaluate
API; RBAC. **Inc 2:** visual authoring UI, richer operators/aggregations,
decision-model-as-pack-artifact, batch evaluation over a worklist.

**Out of scope.** Full DMN/FEEL engine; ML inside the table (models stay
pipelines — a table may reference a model score as a column but doesn't train);
autonomous execution above proposal (a table's outcome is always a proposal
unless tenant auto-execute policy — the same reversible/low-risk gate as agents).

## 2. Actors & user stories

**Personas:** Decision Author / business analyst (DA), the persona whose worklist
the model serves (PU), Approver (MA), Compliance Auditor (CA), Tenant Admin (TA).

- **US-1** As a DA, I author a decision table over the cd-disputes columns —
  e.g. *when dispute_type = fraud_unauthorized AND amount > 1000 → severity
  high, disposition escalate_fraud_review* — from a form, no code.
- **US-2** As a DA, I version it; the prior version stays; the active version is
  explicit and testable.
- **US-3** As a PU, evaluating a case with the model produces a **proposal**
  (not a silent write) that names exactly which rule fired and why.
- **US-4** As an MA, decision-table proposals are governed identically to agent
  proposals: four-eyes, no self-approval, caller-gate, audited.
- **US-5** As a DA, I can dry-run the table against a case and see the outcome +
  fired rule WITHOUT creating a proposal (test before deploy).
- **US-6** As a CA, every executed decision is traceable to the model version,
  the rule that fired, the inputs, the proposal, and the approver.
- **US-7** As a TA, decision models are tenant-scoped (RLS) and never visible or
  runnable cross-tenant.
- **US-8** As a DA, an outcome that references a disposition_code not in the
  workspace catalog is rejected at author time (the model can't emit an invalid
  decision).

## 3. Functional requirements

### DM-FR-001 — Decision-model artifact
`{id, tenant_id, workspace_id?, name, dataset_urn?, version, status
(draft|published), rules: [Rule], default_outcome?}`. `Rule =
{when: [Condition], then: Outcome, note?}`; `Condition = {column, op, value}`
(op ∈ eq, ne, gt, gte, lt, lte, in, contains, exists); `Outcome =
{disposition_code, severity}`. Tenant-scoped, versioned, RLS.

### DM-FR-010 — Deterministic evaluator
Given a row/case field map, evaluate rules TOP-DOWN, FIRST match wins; a rule
matches when ALL its conditions hold (typed comparison, numeric coercion like
the semantic layer). No match → `default_outcome` (or "no decision"). Returns
`{matched: bool, rule_index, outcome, explanation}` — pure, side-effect-free,
exhaustively unit-tested.

### DM-FR-020 — Governed execution (evaluate → proposal)
Executing a model against a case emits a `WriteIntent(case.apply_disposition,
write-proposal)` carrying the resolved disposition_id + severity + a rationale
naming the fired rule, routed through `ProposalService.create_from_intent` — so
four-eyes, caller-gate (`case.case.update`), the guardrail tool-allowlist, and
audit all apply unchanged. The tool is code-fixed (case.apply_disposition),
never config — a decision table can only ever propose a disposition.

### DM-FR-030 — Dry-run
`evaluate?dry_run=true` returns the outcome + fired rule WITHOUT creating a
proposal (US-5) — the "testable" half of "explicit and testable."

### DM-FR-040 — Author-time validation
Reject: empty rules; a condition column not in the dataset schema (when
dataset_urn set); an outcome disposition_code not in the workspace disposition
catalog; a severity outside {low,medium,high,critical}; an unknown operator.
Structured rejection with the offending field.

### DM-FR-050 — Tenant lifecycle + RBAC
Create/list/get/publish/evaluate via API; author gated on a decision-model
capability (reuse `case.disposition.*` / a new `decision.model.*` — inc1 gates
authoring on the existing disposition-management capability the pack roles
already grant to managers). Tenant-scoped throughout.

### DM-FR-060 — Explainability + audit
The proposal rationale states the fired rule (index + note + the conditions that
matched); the executed decision's audit chain includes model id + version +
rule. US-6 reconstruction.

## 4. Business rules

- **BR-1** A decision table only ever PROPOSES (write-proposal); it never writes
  directly — same reversible/four-eyes discipline as agents; auto-execute only
  under the tenant's existing low-risk auto policy (destructive/admin impossible).
- **BR-2** Deterministic + explainable: identical inputs → identical outcome →
  named fired rule. No hidden logic; the LLM is not in this path.
- **BR-3** A model can only emit outcomes valid in the workspace (disposition
  catalog + severity enum) — validated at author time (BR-5 fail-safe).
- **BR-4** Governed by the same spine: caller-gate, four-eyes, guardrail
  allow-list, RLS, audit — inherited via ProposalService, not re-implemented.
- **BR-5** First-match-wins + explicit default — no ambiguous "which rule won."
- **BR-6** Tenant-scoped + versioned; the active version is explicit; prior
  versions retained for audit reconstruction.

## 5. NFRs

| Metric | Target |
|---|---|
| Evaluate p95 (dozens of rules) | ≤ 20 ms (pure, in-process) |
| Author-time invalid outcome/column rejected | 100% |
| Ungoverned write from a decision table | 0 (always a proposal) |
| Cross-tenant model visibility | 0 |

## 6. Acceptance criteria

- **AC-1** A tenant creates a decision model over cd-disputes columns via API;
  it publishes tenant-scoped + versioned. **(inc1)**
- **AC-2** The evaluator is exhaustively unit-tested: each operator, first-match,
  numeric coercion, default, no-match, explainability. **(inc1)**
- **AC-3** Executing the model on a matching case creates a four-eyes proposal
  naming the fired rule; a distinct user approves it. **(inc1, live)**
- **AC-4** Dry-run returns the outcome + fired rule and creates NO proposal. **(inc1)**
- **AC-5** Author-time validation rejects an invalid disposition_code /
  out-of-schema column / bad severity / empty rules — with the field. **(inc1)**
- **AC-6** The proposal inherits the guardrail + caller-gate + four-eyes spine
  (no bypass) — verified via the shared ProposalService path. **(inc1)**
- **AC-7** Tenant isolation: another tenant cannot see or evaluate the model. **(inc1)**
- **AC-8** Full audit reconstruction includes model id + version + fired rule. **(inc1)**

## 7. Increment 1 — what shipped (this build)
Decision-model artifact + store + migration; the pure evaluator; author-time
validation; the governed `evaluate → ProposalService` path; create/get/list/
publish/evaluate API + RBAC; unit tests for the evaluator + validation + the
governed path; live-verify in wr-disputes (author → evaluate a real case →
four-eyes proposal → approve). Inc 2 (designed): visual authoring UI, richer
operators/aggregations + model-score columns, decision-model-as-pack-artifact,
batch evaluation across a worklist.

## 8. Dependencies
agent-runtime (proposals, guardrails, case reader, store), rbac
(disposition-management capability, caller-gate), case-service (dispositions
catalog + case fields), ui-web (inc2 authoring surface), audit.

## 9. Out of scope / future
Full DMN/FEEL; ML training in-table; autonomy above proposal; cross-tenant
model sharing; a marketplace of decision models (inc3+).
