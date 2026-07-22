# BRD 58 WS2 — Operational layer (observability you can actually operate)

**Status:** done — 2026-07-22
**Related:** [58_production_hardening_BRD.md](../brd/58_production_hardening_BRD.md) WS2 · [observability-slos.md](../design/observability-slos.md)

Filed as a standalone initiative doc rather than appended directly to
`docs/brd/58_production_hardening_BRD.md`, which has concurrent in-flight
edits from a parallel session — fold this in the next time that file is
safely editable.

---

## 1. Analysis

Confirmed via a dedicated research pass before any fix (not assumed from the
BRD's own wording):
- OTel tracing is genuinely off by default everywhere (Go/Python/Node all gate
  on the same `DATACERN_OTEL_ENABLED`/`OTEL_EXPORTER_OTLP_ENDPOINT` contract),
  and every cloud Helm overlay ships it commented out.
- The OTel Collector (`deploy/otel-collector.yaml`) only ever exported to
  `debug` (stdout) — no real trace backend anywhere.
- Kafka producer/consumer wrappers (Go and Python) carried zero W3C
  trace-context propagation — confirmed by grep, not assumed.
- Zero Grafana dashboards, zero `PrometheusRule` CRDs anywhere in the repo;
  `ServiceMonitor` exists but is `enabled: false` by default (confirms the
  BRD's claim, though its cited line number had drifted).
- No trace-id/span-id ever appears on a log line except the pre-existing
  app-level random correlation id in each service's own error-response path
  (a different ID system than an OTel span, not the WS2 ask).

## 2. Design

- **Kafka propagation:** inject/extract W3C `traceparent` in both languages'
  Kafka wrappers, alongside (not replacing) the existing app-level
  `trace_id` header — true no-op when tracing is disabled.
- **Log correlation:** Go needs an `slog.Handler` wrapper reading the active
  span from `ctx` (only benefits call sites already using `*Context` methods —
  documented as a real, if partial, floor); Python's OTel context is ambient
  via `contextvars`, so a `logging.Filter` correlates every call site with
  zero call-site changes.
- **Trace backend:** add a real Tempo container to the dev stack
  (`docker-compose.dev.yml`), wire the collector's `traces` pipeline to
  export to it (`otlp/tempo`, alongside `debug`, not replacing it).
- **SLOs:** a concrete, metric-backed threshold doc (`observability-slos.md`)
  ahead of writing the alert-rule bundle, so the two stay consistent.

## 3. Implementation & Test

### Kafka W3C trace-context propagation — DONE
`libs/go-common/kafka/trace.go` (inject/extract via the global OTel
propagator) wired into `Producer.Publish`/`ConsumerGroup.process`.
`libs/py-common/datacern_common/kafka.py` gains the same via
`opentelemetry.propagate`. **Test:** extract==inject round-trip proven in
both languages (recovers the exact trace/span id), explicit no-op-when-
disabled coverage. All 10 Go services consuming go-common build clean; the
real Kafka/Redis integration test (publish→consume→dedup→DLQ) still passes
end to end. Commit `9a19cd4`.

### Trace-id log correlation + SLO doc — DONE
`libs/go-common/otelx.WrapLogHandler` wired into all 11 Go services'
`slog.SetDefault`. `libs/py-common/datacern_common/logging.TraceContextFilter`
wired into `configure_json_logging`. New `docs/design/observability-slos.md`.
**Test:** dedicated unit tests in both languages proving correlation when a
span is active and true pass-through when it isn't. Commit `7b84489`.

### Tempo trace backend + collector wiring — DONE
`deploy/docker-compose.dev.yml` (+`tempo` service, single-binary/monolithic
Grafana Tempo 2.6.1, local-filesystem storage — a production deploy would
split components + use object storage, out of scope for dev wiring),
`deploy/tempo.yaml` (new, OTLP receiver config), `deploy/otel-collector.yaml`
(+`otlp/tempo` exporter on the traces pipeline, alongside `debug`, not
replacing it).

**A real, previously-invisible bug found and fixed via live verification, not
assumed correct from reading the code:** `otelx.Init`'s resource-merge silently
discarded the caller's service name whenever `resource.Merge` hit a semconv
`SchemaURL` mismatch between `resource.Default()`'s builtin detectors (pinned
to whichever semconv version the SDK dependency bundles internally — v1.41.0
in the currently vendored `go.opentelemetry.io/otel/sdk@v1.44.0`) and the
explicit resource this package built with its own imported `semconv/v1.26.0`.
On that (very real, confirmed) conflict, the old code fell back to
`resource.Default()` alone — meaning **every service's traces, for the entire
lifetime of this tracing wiring, would have reported `service.name` as
`unknown_service:<binary-name>` instead of the real service name**, the
moment tracing was ever turned on anywhere. Invisible until now because
tracing had never actually been driven against a real backend before this
workstream — confirmed live: enabled tracing on case-service, queried Tempo,
and saw exactly `unknown_service:case-e2e` where `case-service` should have
been.

**Fix:** extracted the resource-building into a testable `buildResource(name)`
function; switched the explicit service-name resource to schemaless
(`resource.NewSchemaless`, no `SchemaURL`) — `resource.Merge` always accepts a
schemaless side against either resource without a schema conflict, so this is
also resilient to the SDK's semconv version drifting further in the future.
**Test:** `TestBuildResourceSetsTheGivenServiceName` (would have failed
against the pre-fix code, confirmed live rather than via a stashed-code unit
run given the shared working tree) and `TestBuildResourceStillCarriesDefault
Attributes` (proves the merge adds to, not replaces, the default resource).

**Live-verified end to end against the real stack** (user-approved: started
Tempo, restarted the collector, and twice restarted case-service — once with
`DATACERN_OTEL_ENABLED=true` to reproduce and then confirm the fix, once back
to normal afterward): `curl` against Tempo's real query API
(`/api/search?q={resource.service.name="case-service"}`) returned real spans
with `rootServiceName: "case-service"` and the correct `service.name`
attribute — the full chain (service → collector → Tempo, storage, and
TraceQL search) proven live, not just configured.

**Full regression:** `go build`/`go test ./...` clean for `libs/go-common`;
all 10 Go services consuming it still build; `docker compose config`
validates the compose file; the collector's own startup log confirmed no
config errors after the `otlp/tempo` exporter addition.

### Grafana dashboards + PrometheusRule alert bundle — DONE

New `deploy/helm/datacern/templates/prometheusrule.yaml` (10 alert rules
across 4 groups — availability, latency, saturation, domain — encoding
`observability-slos.md`'s exact thresholds 1:1) and `templates/
grafana-dashboard.yaml` (a ConfigMap wrapping a real 7-panel Grafana
dashboard JSON at `dashboards/datacern-red.json`, labeled
`grafana_dashboard: "1"` for the kube-prometheus-stack Grafana sidecar's
auto-discovery convention). Both follow `templates/servicemonitor.yaml`'s
exact existing gating pattern: `monitoring.coreos.com/v1` CRD (no new
operator dependency introduced), disabled by default
(`observability.prometheusRule.enabled` / `.grafanaDashboard.enabled`, both
`false`), rendering nothing when off.

**Design decision, made explicit rather than silently assumed:** every alert
groups by the Prometheus-assigned `job` label (from ServiceMonitor/Service
discovery), not the app-level `service` const-label go-common/metricsx
attaches to Go metrics — because py-common's dependency-free RED renderer
does **not** emit a `service` label at all (confirmed by reading its
`render()` method), which would have silently excluded every Python service
from the RED alerts had they been written against `service`. `job` is the
one label every scraped target carries uniformly regardless of language.

**Test:** `helm lint` clean; `helm template` verified with all three flags
enabled — confirmed exactly 1 `PrometheusRule` (4 groups, 10 rules, every
rule's `expr` non-empty), 24 `ServiceMonitor`s, and 1 correctly-labeled
dashboard `ConfigMap` (parsed the embedded JSON back out: 7 panels, correct
title) render; confirmed all three render **nothing** with the flags at
their `false` default. Re-ran both the enabled and default cases across all
four cloud overlays (`values-aws/gcp/azure/hetzner.yaml`) — all render
clean. Every PromQL expression manually reviewed for balanced parens/valid
function calls/correct label-matcher syntax, and every metric name referenced
cross-checked against the research pass's confirmed-real metric inventory (no
invented metric names).

**Honest verification ceiling, not silently skipped:** no `promtool` binary
available in this environment, and this dev stack runs no Prometheus server
at all (nothing to evaluate the rules against) — verification stops at
`helm lint`/`helm template` validity + manual PromQL review, the same
"no live cluster, verification-stops-at-render" ceiling this session's BRD 58
WS3 Terraform work already established as the honest limit rather than
fabricating a deeper check.
