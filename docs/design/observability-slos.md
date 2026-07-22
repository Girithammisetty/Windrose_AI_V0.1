# Observability SLOs (BRD 58 WS2)

**Status:** design — 2026-07-22
**Related:** [58_production_hardening_BRD.md](../brd/58_production_hardening_BRD.md) WS2

Defines the concrete thresholds the WS2 alert bundle (`PrometheusRule`, next
increment) fires on. Every metric named here already exists in the running
fleet — confirmed by inventory before writing any target, not assumed.

---

## 1. Platform RED SLOs (every Go + Python service)

Backed by `libs/go-common/metricsx` / `libs/py-common/datacern_common/metricsx`
— `http_requests_total{method,route,status}`,
`http_request_duration_seconds{method,route,status}`, `http_requests_in_flight`.

| SLO | Target | Alert threshold | Window |
|---|---|---|---|
| Availability (non-5xx rate) | 99.5% | 5xx rate > 1% | 5m rolling, for 5m |
| Latency (server-side) | p95 < 500ms | p95 > 1s | 5m rolling, for 5m |
| Saturation | headroom under load | `http_requests_in_flight` > 200 sustained | 5m |

Per-service exceptions (documented, not silently different): `agent-runtime`
and `ai-gateway`'s LLM-calling routes are expected to run slower than a plain
CRUD endpoint — their latency alert threshold is p95 > 8s, not > 1s, since a
model round-trip is the dominant cost, not the platform's own overhead.

## 2. Domain-specific SLOs

| Metric | Service | SLO | Alert |
|---|---|---|---|
| `audit_seal_age_seconds` | audit-service | oldest unsealed day < 2h | > 7200 |
| `audit_chain_head_upsert_failures_total` | audit-service | 0 in steady state | rate > 0 over 15m (any failure is worth paging on, not just rate-limiting) |
| `usage_ingest_lag_seconds` | usage-service | p95 < 60s | p95 > 300s over 10m |
| `usage_ingest_dlq_total` | usage-service | 0 in steady state | rate > 0 over 15m |
| `notif_webhook_circuit_opened_total` | notification-service | 0 in steady state | any increase |
| `query_ceiling_rejections_total` | query-service | low, expected background rate | rate > 5/min sustained (signals a caller misusing the API, not urgent but worth a dashboard panel) |
| `rth_dropped_events_total` | realtime-hub | 0 in steady state | rate > 0 over 10m |

## 3. Kafka consumer-lag / outbox-depth — NOT YET INSTRUMENTED

The BRD's own design section asks for consumer-lag and outbox-depth alerting.
Neither exists as a metric today (confirmed: zero `consumer_lag`/`outbox_depth`
hits anywhere in the codebase) — `usage_ingest_lag_seconds` is the closest
existing signal, but it measures publish→ingest lag, not raw Kafka
consumer-group lag (the gap between the group's committed offset and the
partition's high-water mark). Emitting real lag/depth metrics is a
prerequisite piece of work, tracked separately, before a rule can alert on
them — flagged here so the alert bundle doesn't silently omit what the BRD
asked for without an explanation.

## 4. Trace-id correlation caveat

Every log line correlates with its request's trace/span id **only when the
call site already threads context** — Go's `slog.InfoContext(ctx, ...)` (most
existing call sites still use the plain, ctx-less form) or Python code running
inside an active span (ambient via `contextvars`, so this one works for every
call site with no changes, per `otelx.py`/`logging.py`'s existing "start a
span" helpers). SLO dashboards built on log correlation, not metrics, will
have gaps until ctx-aware logging sees broader adoption — a separate,
larger effort, intentionally out of scope for this workstream.

## 5. What's next

The `PrometheusRule` bundle (BRD 58 WS2, remaining increment) encodes exactly
the thresholds in §1–2 above — same metric names, same numbers — plus a
Grafana dashboard surfacing the same RED + domain panels per service.
