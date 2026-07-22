# Deep packs + the no-dummy-data rule (tenant-dataset binding)

**Status:** BUILT + LIVE-VERIFIED (2026-07-22). pack-service inc20 binding shipped;
**13 packs** converted to data-free v2.0.0 — wave 1: card-disputes, banking-aml,
insurance-claims-payer, healthcare-provider-rcm, chargeback-representment
(commit 9546564); wave 2, every healthcare + banking vertical:
care-management-medicare, post-acute-care, payer-fwa-siu, pharmacy-benefit-mgmt,
pharmacovigilance, device-complaints, credit-disputes, mortgage-loss-mitigation
(commit dc17df4); wave 3, every remaining vertical: ap-invoice-audit,
background-screening, benefits-appeals, construction-claims, manufacturing-mrb,
seller-vetting, tax-notices, trade-compliance, trucking-claims,
trust-safety-appeals, underwriting-intake, utility-inspections,
warranty-claims, workers-comp-claims — **all 27 authored packs are now deep
v2.0.0** (investigation-framework, the library pack, was already data-free).
Cross-verified: 27× lint 0 errors/0 warnings; 53 decision tables / 201 rules,
every rule column validated against a dataset contract and every disposition
code against the pack's catalog; live dry-run plans for all 27 surface
`requires_binding` per dataset. Full live install e2e proven on card-disputes
(wave 1) AND credit-disputes (wave 2): real tenant uploads → reuse×2 +
explicit bind×1 → 0 failed → four-eyes semantic approval → dashboards
materialized → macro rewrite to the bound dataset name confirmed in
saved-query SQL.

**Wave-2 governance patterns worth keeping:** decision tables never propose a
clinical/fraud/regulatory determination — routing and expediting only. Packs
whose v1 disposition catalogs held only end-determination codes took one of two
honest paths: omit `default_outcome` so unmatched work stays in the analyst
worklist (device-complaints, credit-disputes), or add ONE neutral escalation
code (pharmacovigilance `escalate_medical_review`, mortgage-loss-mitigation
`escalate_underwriting_review`, mirroring banking-aml's `escalate_l2`).

## 1. Analysis

### Product
All 28 vertical packs were authored to one shallow cookie-cutter shape (~70 CSV
seed rows, 3 dashboards, 6 seeded demo cases) while packctl/pack-service had
grown to 22 installable artifact kinds — 9 real capability kinds (decision
tables, typed case schemas/fields, ontology, guardrails, model archetypes,
write adapters, connection templates, display labels) were unused by every
pack. Simultaneously a standing product rule was set: **the platform must not
add dummy data, example datasets, example cases, or dashboards-on-demo-data as
part of service packs** — packs are a sellable surface and demo content
presented as tenant data undermines the no-fake engineering rules. Value must
come from workflows over the tenant's REAL data.

### Technical
Two blockers made data-free packs impossible before this initiative:
1. Pack semantic models/dashboards/queries/pipelines could only bind to
   datasets the pack itself uploaded — `ensure_dataset` uploads pack CSVs and
   `run_data_chain` resolved `dataset:` refs exclusively against them. There
   was no way to point a pack at an EXISTING tenant dataset.
2. The manifest/lint contract required `file` on every dataset entry.

Also found during scoping: the packctl CLI's installer has no dispatch for 6 of
the 22 kinds (case_fields, case_schemas, guardrails, display_labels, eval_sets,
model_archetypes) — pack-service is the only full-fidelity install path.

## 2. Design

- **Dataset binding contract**: a pack dataset entry becomes
  `{identity, name, required_columns}` with no `file`. Resolution order at
  install: explicit `dataset_bindings[identity] → tenant dataset URN` (ledger
  action `bind`) → same-name tenant dataset (`reuse`) → legacy seed `file`
  upload (demo packs only) → honest failure `requires_binding` naming the
  identity and required columns.
- **Fail-closed column validation**: the bound dataset's current columns
  (dataset-service `GET /datasets/{id}/rows?limit=1`) must contain every
  `required_column`; otherwise the dataset action fails listing the missing
  columns and the install fails.
- **Macro rewrite**: `{{dataset('<pack-name>')}}` in verified/saved queries is
  rewritten to the bound dataset's real name so queries execute against the
  tenant's data.
- **API**: `InstallRequest.dataset_bindings` + `TransitionRequest.dataset_bindings`
  (upgrade/rollback), threaded through `plan` / `run_install` / `run_upgrade`.
  Dry-run plans surface `bind` / `reuse` / `requires_binding` per dataset.
- **Lint**: `datasets` no longer require `file`; new warnings
  `SEED_DATA_SHIPPED` (a product pack shipping seed data) and
  `NO_BINDING_CONTRACT` (file-less entry without required_columns).
- **Deep-pack shape** (the v2.0.0 blueprint, canonical in
  `packs/DEEP_PACK_AUTHORING_ADDENDUM.md`): binding contracts + decision
  tables on contract columns only + typed case schema/fields + ontology +
  guardrails (`budget.max_tokens_per_session`, `pii.{block_pii_egress,redact}`,
  `bind_workspace`) + model archetypes paired to shipped pipelines + empty-
  secret write adapters/source templates + display labels. No seeded cases
  (cases arrive from real rows via case triggers/intake), no eval golden sets
  (no honest labels without real adjudications).

## 3. Implement & Test

**Code** (pack-service inc20 + packctl):
- `packs/packctl/client.py` — `get_dataset`, `dataset_columns`, `bind_dataset`
  (shared resolution + column validation, honest `failed` actions).
- `packs/packctl/installer.py` — CLI datasets branch handles file-less entries
  (name-reuse or honest failure).
- `packs/packctl/lint.py` — datasets required fields relaxed; two new warnings.
- `services/pack-service/app/domain/installer.py` — binding-aware
  `run_data_chain`/`run_install`/`run_upgrade`/`plan`; `_rewrite_dataset_macros`.
- `services/pack-service/app/api/routes/installs.py` — `dataset_bindings` on
  install + transition requests.
- `deploy/local/restart_pack.sh` — isolated pack-service restart (harness env).

**Packs**: five converted to v2.0.0, each lint-clean (0 errors, 0 warnings),
seed CSVs + seeded queues deleted, 9 new kinds authored per pack. The
card-disputes CSV-derived eval golden set was deleted as example cases.
`packs/PACK_AUTHORING_GUIDE.md` carries a superseded-in-part banner.

**Tests**: pack-service unit 15/15 (new: bind/reuse/requires_binding plan
actions, macro rewrite); packctl 23/23 (new: SEED_DATA_SHIPPED,
NO_BINDING_CONTRACT; clean fixture is now a binding contract).

**Live verification** (acme-claims-e2e tenant, real services, no mocks):
- Dry-run plan with nothing bound → 3× `requires_binding` with remediation
  detail.
- Real tenant data uploaded through the product ingestion API under two
  matching names + one different name (`issuer-disputes-real`); install with
  an explicit binding → `reuse`×2 + `bind`×1, 48 created / 0 failed; saved
  query SQL verified rewritten to `issuer-disputes-real`.
- Wrong binding (cardholders dataset bound as disputes) → install failed with
  the exact missing-column list.
- Distinct approver four-eyes-published the semantic model →
  `POST /installs/{id}/complete` materialized all 3 dashboards → `installed`.
  (Charts mostly report 0 rows resolved — honest: the test tenant holds 2 real
  rows; they fill as real data accumulates.)
- Dry-run plans for the four new packs: every dataset `requires_binding`,
  44–51 creates each, dashboards `after_approval`.

## Findings / follow-ups
- **Pack tenant estate wiped**: identity/rbac contain exactly one tenant
  (acme-claims-e2e); the 28 wr-* pack tenants from the earlier multitenant
  install no longer exist (DB reset post-rebrand). `packs/MULTITENANT_LOGINS.md`
  is stale. Fresh binding-mode installs are the path forward.
- **Dev-harness smell**: `deploy/local/seed_platform.py` falls back to
  "PERMISSIVE persona seeding (FAKED admin facts)" when the rbac projector
  does not materialize `authz:proj:*` keys — dev-only, but the real
  grants→projector→authz:proj path deserves a fix.
- packctl CLI still lacks dispatch for 6 kinds — pack-service is the
  full-fidelity installer; unify or retire the CLI install path later.
- The remaining 23 shallow packs still ship seed data (lint now warns
  SEED_DATA_SHIPPED). Convert opportunistically using the addendum blueprint.
