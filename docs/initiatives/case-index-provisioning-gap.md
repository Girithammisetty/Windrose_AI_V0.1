# Case index provisioning gap (brand-new tenant never gets a `cases-<tenant>` index)

**Status:** done — 2026-07-23
**Commits:** (pending)  ·  **Related:** [stability-durability](stability-durability.md)

---

## 1. Analysis

### 1a. Platform / product
A live `make doctor` run flagged an active tenant with zero cases as
`case index MISSING · <tenant> ... Cases page will 503`. `make doctor HEAL=1`
appeared not to fix it (summary still said "1 problem"), but a later, unrelated
`make soak` run showed the index present. That inconsistency — heal reporting
failure while apparently working — undermines trust in the doctor/heal tooling
`stability-durability.md` built specifically to catch this failure class before
a user hits it. Left as-is, any brand-new tenant with no cases yet would show
this same false alarm (or, worse, genuinely 503 the Cases page before its first
case existed and before anyone thought to run a manual heal).

### 1b. Technical
Two independent bugs, not one:

1. `deploy/local/doctor.sh` computed its projections check (section "derived
   projections", the `bad "case index MISSING"` line) as part of a single
   check pass, then ran the heal step (`./reconcile_cases.sh`) *after* that
   pass, in a separate section. The summary block at the bottom prints the
   `fail` counter from the check pass — which ran *before* heal — so
   `HEAL=1` could genuinely fix everything and still report the pre-heal
   failure count in the same invocation. This fully explains the "healed but
   still red, then fine later" observation: it's a script ordering bug, not
   an OpenSearch refresh-interval race. Confirmed by reading
   `services/case-service/internal/search/projector.go`'s `Reindex` — it
   unconditionally calls `CreateReindexGeneration` (creates the physical
   index) and `SwapReindexAlias` (creates the alias) even when the tenant has
   zero cases (`total == 0`), so the heal call itself is synchronous and
   correct; nothing about it is eventually-consistent.

2. `services/case-service` never provisions a tenant's index at
   tenant-creation time. Tracing every caller of
   `search.Client.EnsureIndex` (`opensearch.go:105`) found exactly one:
   `IndexDoc`, invoked only when a case is actually written. And the only
   caller of the admin reindex path (`Projector.Reindex`) is the
   `POST /admin/reindex` HTTP handler, invoked only by an operator or
   `reconcile_cases.py`. identity-service's 7-step tenant provisioning
   workflow (`services/identity-service/internal/domain/engine_steps.go`:
   `AssignCell → CreateKeycloakRealm → ProvisionInfra → CreateDatabases →
   RegisterServices → SeedDefaults → Verify`) has no step for it. So a
   genuinely fresh, empty tenant has no index until its first case write or
   a manual/heal reindex — exactly the gap `doctor.sh`'s own comment warns
   about ("Cases page will 503").

---

## 2. Architecture & Design
Two independent fixes, matching the two causes:

- **doctor.sh ordering:** run the heal section (when `HEAL=1`) *before* the
  numbered projections-check section, so the check that feeds the summary
  always reflects current (post-heal, if requested) state. No behavior change
  when `HEAL=0` — the checks still run against current state as before.

- **Provisioning-time index creation:** rather than adding a synchronous
  cross-service HTTP call into identity-service's provisioning workflow
  (which would need case-service reachable during tenant provisioning, plus
  a new compensation step in a workflow that already has retry/compensation
  semantics to preserve), case-service now **consumes identity-service's
  `tenant.provisioned` event** on its existing `case-inbound` Kafka consumer
  group (already subscribed to `identity.events.v1` for `user.deactivated`/
  `workspace.member.removed`). This mirrors the *established* platform
  pattern: rbac-service already does exactly this for its own tenant
  projection (`tenant.provisioned` → `SeedTenantFromEvent`,
  `services/rbac-service/internal/events/consumers.go`). No new architecture
  invented — same idiom, second consumer.
  - New `Projector.EnsureTenantIndex(ctx, tenant)` → `Search.EnsureIndex`
    (idempotent: no-ops if the alias already exists).
  - New `events.TenantHandler(idx Indexer)`, dispatches only on
    `tenant.provisioned`, ignores every other event type on the shared topic.
  - Wired into `main.go`'s existing `inboundHandler` chain.

Out of scope: deleting the index on tenant deprovisioning (no such gap was
reported or observed; the existing volumes/durability model doesn't clean up
other tenant state on delete either, so this isn't a new inconsistency).

---

## 3. Implementation & Test
**Files touched:**
- `deploy/local/doctor.sh` — moved the heal section before the projections
  check section (renumbered accordingly).
- `services/case-service/internal/search/projector.go` — `EnsureTenantIndex`.
- `services/case-service/internal/events/consumers.go` — `Indexer` interface
  gains `EnsureTenantIndex`; new `TenantHandler`.
- `services/case-service/cmd/server/main.go` — wired `TenantHandler` into the
  `case-inbound` consumer's `inboundHandler`.
- `services/case-service/test/integration/tenant_provision_test.go` (new).

**Verified (live, no mocks):**
- `go build ./...` and `go vet ./...` clean for case-service.
- New integration tests run against the **real, already-running dev-stack
  OpenSearch** (`localhost:9200`) and a real Postgres testcontainer:
  - `TestTenantProvisionedCreatesEmptyCaseIndex` — a brand-new tenant
    (`h.newActor`, no cases) has no alias beforehand; after dispatching a
    real `tenant.provisioned` envelope through `events.TenantHandler`, the
    `cases-<tenant>` alias exists with a real doc count of 0; replaying the
    event a second time (at-least-once delivery) is a no-op, no error.
  - `TestTenantHandlerIgnoresOtherEventTypes` — a `user.invited` event on the
    same shared topic does not create an index.
  - Full existing suite (`go test ./...`, incl. `TestReindexBulkPagesLargeTenant`)
    still passes — 24.4s, no regressions.
- `deploy/local/doctor.sh` re-run live against the current dev stack (1 real
  active tenant) after the reorder: output unchanged in content, section
  numbering now 1/2/3 with no gap; still reports "all healthy."

**Honest gaps / not done:** did not bring up the full application stack
(case-service + identity-service + Kafka relay as running processes — only
infra containers were up) to prove the Kafka wiring end-to-end with a live
`tenant.provisioned` publish from identity-service; the handler logic itself
is proven against real infra via the integration test, and the `main.go`
wiring is a 3-line, type-checked, mechanical addition to an already-live
consumer group. The originally-reported tenant
(`019f90e7-c5b6-7dea-b842-0c2207e6a0e3`) no longer exists in the current dev
DB (stack was reset since); this doc reproduces the mechanism with a fresh
synthetic tenant instead.
