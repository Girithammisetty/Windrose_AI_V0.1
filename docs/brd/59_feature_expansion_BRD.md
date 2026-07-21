# BRD 59 — Feature Expansion (5B)

**Status:** design — 2026-07-21 · sequenced after BRD 58 hardening
**Owner:** platform · **Related:** [tenant-customization-lifecycle](../initiatives/tenant-customization-lifecycle.md), BRD 23 (packs), BRD 53 (custom agents)

Net-new capabilities that increase product value once the platform is
operationally production-ready (BRD 58). Ordered by value-to-effort. Each
workstream follows Analysis → Design → Implement → Test.

---

## WS1 — Unified tenant Customization console

### Analysis
**Product:** a tenant admin customizes via ~10 self-service levers (pack install +
upgrade/rollback/drift, custom agents + guardrails, decision tables, semantic
models, RBAC clone, ontology, labels, embed, BYO-OIDC). They work but are
**scattered across four nav groups** — there is no single "Customization" surface,
which hurts discoverability and the SaaS onboarding story.
**Technical:** all backends exist and are RBAC-gated (audited in the customization
review). This is a UI-composition + information-architecture task, not new backend.

### Design
A `/admin/customization` hub that surfaces each lever as a card with status
(installed packs + available upgrades/drift, custom agents, decision tables,
models, roles, ontology, branding, IdP) — read models already exist in the BFF.
Deep-links to the existing editors; no logic duplication.

### Implement / Test
- [ ] hub page + cards wired to existing queries · [ ] drift/upgrade badges from BRD-23 lifecycle · [ ] Playwright: hub renders every lever with correct RBAC gating.

---

## WS2 — Per-tenant SIEM export destination

### Analysis
**Product:** enterprise buyers require audit/event export to *their* SIEM. Today
export publishes to a single shared Kafka topic (`audit.export.v1`) — no per-tenant
destination config.
**Technical:** audit-service SIEM export + webhook delivery exist; needs a
per-tenant destination registry (endpoint, auth, format) + delivery routing.

### Design
`tenant_siem_configs` (endpoint, format=CEF|LEEF|JSON, auth ref via BYO-secrets);
a self-service `/admin/audit/export` screen; delivery routes per-tenant; four-eyes
on config change (standing-config governance rule).

### Implement / Test
- [ ] migration + config API · [ ] delivery routing · [ ] UI + BFF · [ ] integration test: two tenants, two destinations, no cross-delivery.

---

## WS3 — White-label branding (logo / theme)

### Analysis
**Product:** embedding + display-label overlay exist, but there is **no logo/theme
white-label** — only text labels + embed origins. Partners want their mark + palette.
**Technical:** per-tenant theme tokens + logo asset (MinIO) + serve in app shell +
embed.

### Design
`tenant_branding` (logo object ref, primary/accent tokens); app shell + embed read
it; admin upload screen; CSP-safe asset serving.

### Implement / Test
- [ ] branding store + upload · [ ] shell/embed theming · [ ] visual e2e in light/dark.

---

## WS4 — Backup / DR + live-data upgrade-migration

### Analysis
**Product:** a customer will ask "what's your RPO/RTO and how do you upgrade without
downtime/data-loss?" — no story today.
**Technical:** managed-Postgres PITR + object-store versioning give primitives;
needs a documented DR runbook, tested restore, and a zero-downtime migration
strategy (expand/contract) for the 273-migration surface.

### Design
DR runbook (backup schedule, restore drill, RPO/RTO targets); expand/contract
migration guideline + a CI check that flags destructive migrations; a restore
game-day.

### Implement / Test
- [ ] DR runbook + restore drill · [ ] destructive-migration CI lint · [ ] documented RPO/RTO.

---

## WS5 — Customization marketplace (greenfield, later)

### Analysis
**Product:** tenants author packs today via CLI (packctl). A marketplace lets them
share/discover/sell customizations — a growth lever, not a near-term need.
**Technical:** pack-service registry + signing + versioning exist as the substrate;
marketplace = catalog + trust/signing + install-from-registry UX.

### Design / Implement / Test
Deferred — sketch only. Requires pack signing-trust chain, a registry service, and
a review/curation flow. Revisit post-GA.

---

## WS6 — GPU-backed SLM training (unblock the gated path)

### Analysis
**Product:** the distillation/correction→retrain loop is built; real LoRA training
is honestly gated behind `GpuTrainerNotConfigured` (no GPU locally).
**Technical:** control plane (training-job service, migrations) + GPU nodepool
Terraform exist; needs a real GPU trainer wired + a cloud GPU nodepool applied.

### Design / Implement / Test
- [ ] wire a real trainer (HF/PEFT LoRA) behind the existing job control plane · [ ] apply the GPU nodepool (cloud, resource-gated) · [ ] train→evaluate→promote a real SLM end to end.

---

## Sequencing
BRD 58 (hardening) is the prerequisite for any customer exposure. Within 5B: WS1
(console) and WS2 (SIEM) are the highest value for enterprise deals and are
mostly-existing-backend; WS3 next; WS4 before GA; WS5/WS6 post-GA / resource-gated.
