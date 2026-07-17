# rbac-service runbook

## Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| `rbac_authz_fallback_total` rate > 0.1% sustained | Redis loss / projection cold | Check Redis; fallback path carries traffic (BR-9, authz availability unaffected). Run full rebuild: `rebuild -tenant <uuid>` or `POST /admin/projection/rebuild?tenant=`. |
| `rbac_projection_staleness_seconds` p99 > 5s | Worker crash-loop, dirty-queue backlog | Check worker logs; `SELECT count(*) FROM projection_dirty;` Claimed rows older than the 30s visibility timeout are reclaimed automatically (BR-8). Scale worker replicas — recompute is idempotent + versioned LWW. |
| Weekly verification drift > 0 | Bug or manual Redis edits | `POST /admin/projection/verify?tenant=<t>` repairs immediately; page the on-call if it recurs. |
| Outbox backlog (`SELECT count(*) FROM outbox WHERE published_at IS NULL`) | Kafka down | Relay retries in id order; no action needed once brokers return. Events are at-least-once; consumers dedup on event_id. |
| Consumer DLQ depth > 0 for 15 min | Poison inbound event | Inspect `<topic>.rbac-service.dlq`; fix handler or re-publish after correction. |

## Ops commands

- Full rebuild + verify: `rebuild -tenant <uuid> -verify` (env: `DATABASE_URL`, `REDIS_ADDR`).
- Orphan-grant sweep check (must be 0): see `Store.OrphanGrantCount`; nightly job runs `SweepOrphanGrants`.
- Last-admin override: super-admin token + `X-Override-Reason` header; writes `security.last_admin_overridden` audit event.

## Rollback

Stateless service; roll back the image. Migrations are forward-only — never
roll back schema; ship a follow-up forward migration instead. The projection
self-heals (24h TTL + fallback warm), so Redis can be flushed safely in an
emergency.
