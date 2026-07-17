# chart-service RUNBOOK

## Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| `GET /charts/:id/data` → 502 UPSTREAM_QUERY_FAILED | semantic-service or query-service down/unreachable | Check `SEMANTIC_SERVICE_URL` / `QUERY_SERVICE_URL` health; the resolver forwards the caller JWT so upstream 401s surface as 502 here. |
| Every request 403 PERMISSION_DENIED | OPA sidecar down or Redis `permissions_flat` projection missing | Verify `OPA_URL` (`/health`) and that rbac-service has published the projection to Redis. Authz fails closed by design. |
| Cache never hits (`meta.cache` always `miss`) | Redis unreachable, or chart_version bumping each write | Check `REDIS_ADDR`; confirm `display_meta`-only edits don't bump `chart_version`. |
| Stale data after a measure/query change | invalidation consumer not running or Kafka down | Confirm `KAFKA_BROKERS` set (not `false`); check the `chart-service-invalidation` consumer group lag. |
| Migrations fail: `permission denied to create role` | `MIGRATE_DATABASE_URL` is not an owner/superuser | Migrations must run as the DB owner (creates the `chart_app` role + RLS). Runtime uses the non-owner `DATABASE_URL`. |
| PNG export always fails `PNG_RENDERER_UNAVAILABLE` | headless renderer sidecar not configured | Set `PNG_RENDERER_URL`. CSV export is unaffected. |

## DLQ drain

Invalidation consumers route poison messages to
`<topic>.chart-service-invalidation.dlq` after 5 retries (go-common default).
To drain: inspect the DLQ topic, fix the projection/reverse-index cause, and
replay — handlers are idempotent (dedup by `event_id`, and invalidation is a
delete, safe to repeat).

## Cache stampede / hot dashboard

Misses are guarded by a Redis singleflight lock (`SET NX PX 30s`). If a leader
crashes mid-resolve the lock expires in ≤30s and the next request re-leads. To
force a cold refresh, delete `chart:{tenant}:{chart_id}:*` and
`chartkeys:{tenant}:{chart_id}`.

## Rollback

Forward-only migrations. To roll back the service, deploy the previous image;
schema is additive. The `chart_app` role is cluster-global and left in place by
`000002_rls.down.sql`.

## Export artifact expiry

Export artifacts carry a 15-min signed URL; the object-store retention job
purges files after 7 days. Signed URLs are HMAC'd with `EXPORT_SIGNING_SECRET`
— rotating it invalidates outstanding links.
