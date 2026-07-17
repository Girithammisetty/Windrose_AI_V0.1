# notification-service (Go)

The platform's single fan-out point from events to humans and external systems —
**in-app**, **email** (multi-provider abstraction), and **signed webhooks** —
governed by per-user/per-workspace subscription rules, digest batching, versioned
per-tenant templates, and per-recipient rate limiting. Implements **BRD 19**;
inherits the master BRD (`docs/brd/00_MASTER_BRD.md`).

## End state: real, no runtime stubs

Every adapter is **real by default** (no env flag selects a fake):

| Capability | Real adapter |
|---|---|
| OLTP | PostgreSQL, RLS `FORCE` + shipped **non-owner** role `notif_app` |
| Event bus (consume all `*.events.v1`) | Redpanda (real Kafka) via `go-common/kafka` consumer group |
| Emitted events | transactional outbox → `notification.events.v1` |
| Cache / dedup / rate limits / projection / directory | Redis |
| Realtime push | Redis pub/sub on realtime-hub's `rt:ch:<tenant>/notifications:<user>` |
| Email | SMTP driver (default), exercised against a real local SMTP capture (Mailpit) |
| Webhooks | real HTTP POST, HMAC-SHA256 signing, SSRF guard, retry/circuit-breaker |
| AuthN / AuthZ | JWKS RS256 verify + OPA sidecar over Redis projection |
| Digest flush / webhook retry | durable **Postgres-backed worker** (Temporal-equivalent) |

**Credential-gated exceptions only** (real driver code, unit-tested; live delivery
needs cloud creds): **SES** (SigV4), **SendGrid**, **ACS** email drivers. The
runtime email path is fully real via SMTP. No `NotImplementedError` / `ErrNotWired`
/ fake adapters are reachable from `cmd/server` (CI no-stub gate passes).

## Run

```bash
# Real infra (repo root): make dev-up   # postgres, redpanda, redis, opa, ...
# Create this service's DB once (owner windrose):
docker exec windrose-dev-postgres-1 createdb -U windrose notification
export PATH=/opt/homebrew/bin:$PATH
make run       # boots on :8087 with real adapters (default env)
# GET /healthz /readyz /metrics ; API under /api/v1
```

Migrations run under `MIGRATE_DATABASE_URL` (owner, creates the `notif_app` role
+ RLS); the runtime pool connects as the **non-owner** `DATABASE_URL`
(`notif_app`) so RLS binds it. SMTP defaults to Mailpit (`localhost:1025`).

## Test

```bash
make test-unit          # no Docker; test doubles only
make test-integration   # Testcontainers: Postgres, Redis, Redpanda, Mailpit + httptest webhook; auto-skips without Docker
```

Integration tests exercise **real components** end-to-end: a real Kafka event →
in-app row (Postgres) + captured email (Mailpit SMTP) + realtime publish (Redis);
signed webhook POST to a real HTTP server (HMAC verified); circuit breaker on a
failing endpoint; rate-limit → digest; RLS cross-tenant via the shipped role; OPA
authz against the sidecar.

## FR coverage (BRD 19 §3)

| FR | MoSCoW | Status | Code / Test |
|---|---|---|---|
| NOTIF-FR-001 consume all topics + dedup | M | ✅ | `events.ConsumedTopics`, `cmd/server` consumer group; `TestAC03_KafkaRedeliveryDedup` |
| NOTIF-FR-002 event→notification mapping registry | M | ✅ | `internal/registry` |
| NOTIF-FR-003 pipeline (map→audience→prefs→gate→render→deliver→record) | M | ✅ | `internal/pipeline`; `TestAC01…` |
| NOTIF-FR-010 subscription rules CRUD + filters | M | ✅ | `handlers_rules.go`, `store/rules.go` |
| NOTIF-FR-011 rule evaluation (≤1 per event/channel) | M | ✅ | `subscriptions.Matches`, pipeline dedup; `TestAC11_FilterMatch` |
| NOTIF-FR-012 preferences (mute/override/quiet-hours/digest) | M | ✅ | `internal/preferences`; `TestAC13…`, `TestBR3…` |
| NOTIF-FR-013 group/role audience expansion (≤500) | S | ✅ | `pipeline.RedisGroupResolver`, `MaxAudience` truncation |
| NOTIF-FR-020 in-app persist + realtime + inbox API | M | ✅ | `channels/inapp`, `handlers_inbox.go`; `TestAC01…` |
| NOTIF-FR-021 email provider abstraction + failover + suppression | M | ✅ | `channels/email` (SMTP/SES/SendGrid/ACS); `TestBR9…`, `TestAC09…` |
| NOTIF-FR-022 webhooks (HMAC, handshake, rotation, SSRF) | M | ✅ | `channels/webhook`; `TestAC04…`, `TestAC06…`, `TestAC12…` |
| NOTIF-FR-023 retry/backoff + circuit breaker + dead-letter | M | ✅ | `channels/webhook`, `pipeline/webhook.go`, `worker`; `TestAC05…` |
| NOTIF-FR-024 delivery log + manual redeliver | S | ✅ | `handlers_webhooks.go` (`/deliveries`, `/redeliver`) |
| NOTIF-FR-030 digest batching (window / 200 items) | M | ✅ | `store/digest.go`, `worker.flushDigests`; `TestAC09…` |
| NOTIF-FR-031 per-recipient rate limits → digest | M | ✅ | `internal/ratelimit`; `TestAC09_RateLimitToDigest` |
| NOTIF-FR-032 per-tenant email budget | S | ✅ | `ratelimit.AllowTenantEmail`, pipeline email gate |
| NOTIF-FR-040 versioned templates + whitelisted vars | M | ✅ | `internal/templates`; `TestAC08_WhitelistValidation` |
| NOTIF-FR-041 per-tenant overrides + preview/test-send + fallback | M | ✅ | `store/templates.go`, `handlers_templates.go`, `pipeline.renderFor` |
| NOTIF-FR-042 locale selection chain | C | ◑ | directory locale → `en` default (full user→tenant→en chain deferred) |
| NOTIF-FR-050 delivery tracking + status transitions | M | ✅ | `store/deliveries.go` |
| NOTIF-FR-051 ops API (stats, suppressions) | M | ✅ | `handlers_admin.go` |

**Must: 16/16 implemented. Should: 3/3. Could: 1 partial (042).**

## Acceptance criteria → tests

| AC | Test |
|---|---|
| AC-1 event→in-app+email+realtime within 5s | `TestAC01_RealKafkaToInAppEmailRealtime` (int), `TestAC01_CaseAssignedInAppAndEmail` (unit) |
| AC-3 exactly-once on redelivery | `TestAC03_KafkaRedeliveryDedup` (int), `TestAC03_ExactlyOnceDedup` (unit) |
| AC-4 webhook HMAC + replay guard | `TestAC04_WebhookHMACRealPost` (int), `TestAC04_SignatureVerifyAndReplay` (unit) |
| AC-5 circuit opens on 10 failures, recovers, in-order flush | `TestAC05_WebhookCircuitBreakerAndRecovery` (int) |
| AC-6 dual-secret rotation | `TestAC06_DualSecretRotation` (unit) |
| AC-8 template whitelist rejects unknown var | `TestAC08_WhitelistValidation` (unit) |
| AC-9 rate-limit → digest (never dropped) | `TestAC09_RateLimitToDigest` (int + unit) |
| AC-11 rule attr filter (high vs medium) | `TestAC11_FilterMatch` (unit) |
| AC-12 SSRF guard | `TestAC12_SSRFGuard` (unit) |
| AC-13 quiet hours defer email; critical bypass | `TestAC13_QuietHoursDeferEmail` (unit) |
| AC-14 cross-tenant → empty via shipped role | `TestAC14_RLSCrossTenantDefaultRole` (int) |
| OPA authz decision | `TestAC_OPAAuthzDecision` (int) |

## Layout

```
cmd/server/main.go            real-adapter wiring, consumer, outbox relay, worker
internal/
  registry/                   event→notification mapping + whitelisted var schemas
  pipeline/                   core pipeline + audience/render/email/webhook/in-app
  channels/{inapp,email,webhook}
  subscriptions,preferences,ratelimit,templates,worker,authz,register,events,store
migrations/                   000001 schema · 000002 RLS FORCE · 000003 non-owner role
```
