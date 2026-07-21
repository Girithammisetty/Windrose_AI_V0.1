# Tenant customization: pack lifecycle (upgrade / rollback / drift)

**Status:** implemented (BFF + UI) — 2026-07-21 · not browser-verified (parallel ui-web work in flight)
**Commits:** `cd74780` (BFF), `9abeb7b` (UI)
**Related:** BRD 23 (pack-service), memory `project_windrose_pack_service`

---

## 1. Analysis

### 1a. Platform / product
In a multi-tenant SaaS, a tenant customizes by installing capability packs and
configuring on a shared codebase (never a per-tenant code fork). An audit of the
customization surfaces found most are already self-service (install, custom agents,
decision tables, semantic models, RBAC clone, ontology, labels, embed, BYO-OIDC,
operator ceilings). **The one gap that blocks a real SaaS tenant: pack lifecycle
*after* install** — a tenant could install a pack but not upgrade, roll back, or
check drift without the platform team.

### 1b. Technical
`pack-service` fully implements the lifecycle in REST — `POST /installs/{id}/upgrade`,
`/rollback`, `GET /installs/{id}/drift` (`services/pack-service/app/api/routes/installs.py`)
— but **none were exposed in BFF GraphQL or the UI**. Upgrade/rollback use a
preview→execute shape (`dry_run` returns the diff; execute supersedes with a new
install row). Drift compares the install's ledger to Core's current state.

---

## 2. Architecture & Design
Pure wiring over the existing, tested backend — no new subsystem.
- **BFF:** `pack.ts` client methods `drift/upgrade/rollback`; `map.ts` normalizers
  (`mapPackTransition` folds the dry-run vs executed shapes into one type);
  `PackDrift` / `PackTransition(+Diff)` schema types; `packDrift` query; `upgradePack`
  / `rollbackPack` mutations. pack-service enforces `pack.install.read` (drift) /
  `pack.install.execute` (upgrade/rollback); the caller JWT is forwarded.
- **UI (`/packs`):** each installed-pack row gets Check drift / Upgrade / Rollback.
  Upgrade & rollback are **preview→confirm**: first click runs `dryRun` (no side
  effects) and shows the version delta + added/removed/unchanged counts; a real
  transition only fires on explicit Confirm (mirrors the plan→install pattern).
  Idempotency key sent only on execute.

Invariant: no tenant config mutates without a second explicit action; governance
gates unchanged.

---

## 3. Implementation & Test
Files: `bff-graphql/src/{clients/pack.ts, schema/map.ts, schema/typeDefs.ts,
resolvers/index.ts}`; `ui-web/src/lib/graphql/{types,operations,hooks}.ts`,
`ui-web/src/app/(app)/packs/page.tsx` + `packs.test.tsx`.

**Verified:** BFF `tsc` clean, schema builds, 301 tests pass. UI `tsc` clean (my
files), 6 packs tests pass incl. new drift-summary + upgrade preview-then-confirm
(asserts the dry-run does NOT execute).

**Deferred / honest gaps:** not browser-verified end-to-end — a parallel session is
mid-edit on `ui-web` auth files (an unrelated tsc error in their `auth/login/route.ts`),
so a live click-through was unreliable and not mine to fix. Runner-up customization
gaps remain: per-tenant SIEM export destination, and a unified "Customization" console
(levers are scattered across four nav groups).
