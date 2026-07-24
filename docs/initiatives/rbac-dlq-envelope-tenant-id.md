# rbac-service DLQ poison envelopes carry `tenant_id = uuid.Nil`

**Status:** done — 2026-07-21
**Commits:** (uncommitted; this change set)  ·  **Related:** BRD 58 WS5 (`docs/brd/58_production_hardening_BRD.md` §WS5.3, event-envelope conformance) — this fix closes one of the two conformance bugs that work surfaced but deliberately left unfixed (additive-test-coverage scope).

---

## 1. Analysis

### 1a. Platform / product
Every consumer group in the platform routes messages it can't process after 5
retries to a `<topic>.<group>.dlq` topic as a `consumer.poison` event, so an
operator can see what failed and why. rbac-service's version of that event was
itself non-conformant with the platform's own master event envelope contract
(MASTER-FR-031, which requires a non-nil `tenant_id`) — a dead-letter record
meant to flag a delivery failure would itself fail delivery if anything ever
validated it on the way in. Not a live incident (see 1b for why), but a
correctness gap in a mechanism whose entire job is to be trustworthy when
something else has already gone wrong.

### 1b. Technical
`services/rbac-service/internal/events/consumers.go` `KafkaConsumer.toDLQ`
built its `consumer.poison` envelope with `tenant := uuid.Nil` unconditionally,
regardless of call site:
- `processMessage`'s decode-failure branch (line 177, pre-fix) — genuinely no
  tenant is recoverable, the message never parsed.
- `processMessage`'s retry-exhausted branch (line 200, pre-fix) — the message
  **did** decode successfully into `env` earlier in the same function, so
  `env.TenantID` was sitting in scope and simply wasn't threaded through.

The shared conformance validator (`libs/go-common/event.Validate`, added by
BRD 58 WS5 as the single source of truth mirroring audit-service's consumption-
side `domain.ValidateEnvelope`) rejects `tenant_id == uuid.Nil`. WS5's new
`envelope_conformance_test.go` exercises `NewEnvelope`+`toMaster` for rbac's
real emission call sites but never for `toDLQ`'s poison envelope, so the gap
went uncaught until traced through by hand.

**Why this was never a live incident:** checked `audit-service`'s actual
consumption path. `internal/domain/topics.go` `TopicSubscription.Matches`
explicitly excludes any topic with a `.dlq` suffix ("to avoid re-ingesting
quarantined messages") — so `rbac.events.v1.rbac-consumer.dlq` is never
consumed by audit-service at all, and `ValidateEnvelope` never runs against
this envelope in production. The bug is real but was latent: any future
tooling that *does* read DLQ topics through the shared validator (a redrive
path, a DLQ-depth dashboard that decodes payloads, a new conformance sweep)
would have hit it immediately. Also confirmed audit-service's own `toDLQ`
(`internal/ingest/consumer.go`) has the right instinct — it re-decodes the
raw message to recover `env.TenantID` before building its own poison event —
but falls back to `uuid.Nil` (not a sentinel) when that decode fails, and has
no conformance test locking in the poison-envelope shape either. That's a
separate latent gap, out of scope here (not part of this ticket, and doesn't
change rbac-service's fix).

---

## 2. Architecture & Design
Three options were on the table:
- **(a) Synthetic platform-tenant sentinel for events with no real tenant.**
- **(b) Exempt this envelope type from tenant-scoped validation on the
  consuming side.**
- **(c) Thread the real tenant from the original message where one exists.**

Chose **(c) as the primary fix, with (a) as the fallback for the case where no
real tenant is recoverable** — not a fallback-only design. Rejected (b)
outright: weakening the shared validator to carve out an exception is exactly
the kind of drift BRD 58 WS5 built the shared validator to prevent, and it
would still leave the *decode-failure* case with no principled non-nil value
to fall back to. (a) alone (always synthesizing a sentinel) was rejected as
the sole strategy because it would discard tenant attribution that's already
available in-memory in the far more common retry-exhausted path — losing
real signal an operator wants when triaging DLQ contents.

Implementation:
- `internal/events/events.go` — added `PlatformTenant`, a reserved non-nil
  UUID (`00000000-0000-0000-0000-000000000001`) for events with no real
  tenant, documented as currently used only by `toDLQ`'s undecodable-message
  path.
- `internal/events/consumers.go` — `toDLQ` now takes an explicit `tenant
  uuid.UUID` parameter instead of hardcoding `uuid.Nil`, and substitutes
  `PlatformTenant` only when the caller passes `uuid.Nil`. The two call
  sites: `processMessage`'s decode-failure branch passes `uuid.Nil` (no
  tenant is recoverable — deliberate, not an oversight); the retry-exhausted
  branch passes `env.TenantID` (already decoded, threaded through instead of
  discarded).

**Update 2026-07-23:** the note below is now stale. `tool-plane`'s analogous
`domain.PlatformTenant == uuid.Nil` finding — spanning every platform-scoped
`tool.events.v1` lifecycle event (registered, deprecated, retired, killed,
SLA-breached, etc.), not just a DLQ path — was fixed the day after this doc
was written, in commit `31372e5`: `services/tool-plane/internal/domain/types.go:73`
now carries the same reserved-sentinel pattern
(`00000000-0000-7000-8000-000000000001`), with a forward migration
(`migrations/000004_platform_tenant_sentinel.up.sql`) re-pointing the RLS
policy literal + already-persisted rows + queued outbox rows, and a
regression test (`TestEnvelopeConformance_PlatformScoped`) locking it in.
Verified 2026-07-23: tests pass, migration applied (`schema_migrations`
version 5, not dirty), zero remaining `00000000-0000-0000-0000-000000000000`
references in tool-plane Go source. No outstanding work here.

_Original note (superseded):_ did not touch `tool-plane`'s analogous
`domain.PlatformTenant == uuid.Nil` finding from the same BRD 58 WS5 pass —
same conformance bug, but it spans every platform-scoped `tool.events.v1`
lifecycle event (registered, deprecated, retired, killed, SLA-breached, etc.),
not just a DLQ path, and is explicitly tracked as a separate, broader
follow-up in BRD 58.

---

## 3. Implementation & Test
- `services/rbac-service/internal/events/events.go` — `PlatformTenant` sentinel.
- `services/rbac-service/internal/events/consumers.go` — `toDLQ` signature
  change + tenant threading, both call sites updated.
- `services/rbac-service/internal/events/envelope_conformance_test.go` — two
  new tests: `TestToDLQ_UndecodableMessage_UsesPlatformTenant` (decode-failure
  path emits `PlatformTenant`, non-nil, passes `gcevent.Validate`) and
  `TestToDLQ_HandlerFailure_ThreadsRealTenant` (retry-exhausted path preserves
  the real tenant, passes `gcevent.Validate`).

Verified: `go vet ./internal/events/...` clean; new tests pass individually
and the full `rbac-service` suite (`go test ./...`) is green — 9 packages
`ok`, no regressions.
