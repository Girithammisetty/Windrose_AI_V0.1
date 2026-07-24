# Observability alert drill — proving DatacernHighErrorRate fires live

**Status:** done — 2026-07-23
**Related:** [brd58-ws2-observability.md](brd58-ws2-observability.md) (built the rule bundle) · [58_production_hardening_BRD.md](../brd/58_production_hardening_BRD.md) WS2 · [observability-slos.md](../design/observability-slos.md)

---

## 1. Analysis

### 1a. Platform / product
The platform ships 10 SLO alert rules (`deploy/helm/datacern/templates/
prometheusrule.yaml`) meant to catch real production incidents — high error
rate, high latency, saturation, and domain-specific failure modes (audit seal
lag, DLQ growth, etc.). An alert rule that has never actually been evaluated
by a running Prometheus is an unverified claim, not a working safety net: the
PromQL could have a typo, a label mismatch, a threshold that never trips, or
depend on a metric that isn't actually emitted the way the rule assumes. The
gap is exactly "alerts wired but never fired in a real incident" — this
initiative closes it for the flagship alert (`DatacernHighErrorRate`) with a
reusable, checked-in drill any engineer can re-run.

### 1b. Technical
Confirmed prior to this work (carried over from `brd58-ws2-observability.md`'s
own honest ceiling): Prometheus/Alertmanager/Grafana have never run in this
environment. No docker-compose service for them exists;
`deploy/helm/datacern/values.yaml` gates the CRDs behind
`observability.prometheusRule.enabled` (default `false`); verification never
went past `helm lint`/`helm template`. Go services (`libs/go-common/
metricsx`) emit real `http_requests_total{method,route,status}` (const-label
`service`, not `job` — `job` is assigned by whatever scrapes them) and
`http_request_duration_seconds_bucket`, confirmed by reading
`libs/go-common/metricsx/metricsx.go`. No existing route in the repo produces
a genuine 5xx on demand (checked: no test anywhere asserts
`http.StatusInternalServerError`, no chaos/fault-injection endpoint existed).

---

## 2. Architecture & Design

Four small, separated pieces, each independently useful and none touching
anything outside its own lane:

1. **`services/notification-service/internal/api/chaos.go`** — a synthetic-
   fault endpoint, `POST /internal/chaos/error`, added to notification-service
   specifically because it is a pure notification-delivery path, not a
   tenant-data path (low blast radius). It returns a real 500 through the
   service's normal `writeErr`/`domain.Error{Code: domain.CodeInternal}` path
   — not a panic (the existing `RecoverMiddleware`, mirrored across every Go
   service, already proves panics become safe 500s; this endpoint instead
   proves the metrics + alerting path reacts to a genuine handler-returned
   error). Registered in `internal/api/server.go` alongside the router's other
   unauthenticated `/internal/*` routes (mirrors the existing pattern in
   `case-service`/`chart-service`'s `POST /internal/v1/mcp/invoke`, and
   `realtime-hub`'s `POST /internal/v1/publish` — none of those go through the
   human-JWT `/api/v1` group either). Gated OFF by default: unless the
   process's own environment has `CHAOS_ENDPOINTS_ENABLED=true` at boot, every
   request 404s exactly as if the route didn't exist. The check is re-read
   every request rather than cached at startup, but a running process's own
   environment cannot be changed from outside it — flipping the switch always
   requires a restart. Comment in the file states plainly: "for observability
   alerting drills only; never enable in a real environment."

2. **`deploy/observability/render_rules.py`** — runs
   `helm template datacern deploy/helm/datacern --show-only
   templates/prometheusrule.yaml --set observability.prometheusRule.enabled=true`
   (the exact rendering path CI/`helm lint` already use) and lifts
   `.spec.groups` out of the rendered `PrometheusRule` CRD into a plain
   Prometheus rule file, `deploy/observability/rules.generated.yml` (top-level
   `groups:` key — the format `rule_files:` expects). No rule body is
   hand-duplicated anywhere, so there is zero drift risk between what CI lints
   and what this drill evaluates. Uses PyYAML (`deploy/e2e/.venv`), not `yq`
   (not installed in this environment).

3. **`deploy/observability/prometheus.yml`** — a scrape config for a throwaway
   Prometheus: one job, `notification-service`, targeting
   `host.docker.internal:8323` (the live dev-stack process, not a compose
   service — notification-service runs as a bare process under
   `deploy/e2e/run/`), `rule_files: [rules.generated.yml]`. No Alertmanager
   section: proving the rule transitions `inactive -> pending -> firing` is
   entirely a property of Prometheus's own rule-evaluation engine, visible via
   `/api/v1/rules`; routing a firing alert to a channel is a separate, already
   well-understood concern this drill doesn't need to re-prove.

4. **`deploy/observability/drill.sh`** — orchestrates the whole thing:
   renders fresh rules, restarts *only* notification-service with
   `CHAOS_ENDPOINTS_ENABLED=true` (reusing `deploy/e2e/boot_services.sh`'s own
   `start_notification()` function by sourcing it, rather than hand-copying
   its env — so the drill's boot env can never drift from the real e2e
   harness's), starts a `docker run --rm` throwaway Prometheus on `:9091`
   (never added to `docker-compose.dev.yml`), drives continuous synthetic 500s
   (one `POST /internal/chaos/error` per second, for the whole drill duration
   — a one-shot burst would not sustain the `for: 5m` window), polls
   `/api/v1/rules` every 15s for up to 6 minutes printing every observed state
   transition with a real timestamp, tears the Prometheus container down, and
   unconditionally restores notification-service to normal
   (`CHAOS_ENDPOINTS_ENABLED` unset) in a `trap ... EXIT` cleanup — runs even
   on a mid-drill failure.

**Out of scope / explicitly not attempted:** Alertmanager routing/silencing,
Grafana rendering, the other 9 alert rules (this proves the pattern works;
extending it to every rule is a mechanical follow-up, not a new design), and
touching `deploy/helm/datacern/values.yaml` (owned by a concurrent session).

---

## 3. Implementation & Test

Files added:
- `services/notification-service/internal/api/chaos.go` (new)
- `services/notification-service/internal/api/server.go` (route registration only)
- `deploy/observability/render_rules.py` (new)
- `deploy/observability/prometheus.yml` (new)
- `deploy/observability/drill.sh` (new)
- `deploy/observability/rules.generated.yml` (generated artifact, checked in as evidence of a real render; regenerate via `render_rules.py`)

`go build ./...` for notification-service clean after the change.

### Live drill run — REAL evidence

Run against the already-live dev stack (`deploy/e2e/run.sh`-booted). Sequence
executed by `drill.sh`, unmodified:

1. Pre-flight: `notification-service` confirmed up at `:8323`.
2. `render_rules.py` → `rules.generated.yml` (4 groups, 10 rules) written from
   the live Helm template.
3. notification-service (pid 3788) stopped, rebuilt, and restarted with
   `CHAOS_ENDPOINTS_ENABLED=true`; came back ready (`/readyz 200`); confirmed
   `POST /internal/chaos/error` → `500`.
4. Throwaway `prom/prometheus` container started on `:9091`; scrape target
   `notification-service` confirmed `health: up`.
5. Synthetic-load generator started (1 req/s against the chaos endpoint).
6. Polled `/api/v1/rules` every 15s.

**Observed `DatacernHighErrorRate` state transitions (real timestamps, from
the actual `drill.sh` run):**

```
2026-07-23T20:27:00-0400  DatacernHighErrorRate: <none> -> inactive
2026-07-23T20:27:15-0400  DatacernHighErrorRate: inactive -> pending
2026-07-23T20:32:16-0400  DatacernHighErrorRate: pending -> firing
```

`pending` held for 5m01s (20:27:15 → 20:32:16) against the rule's own
`for: 5m` — the extra second is scrape/evaluation-interval slack, exactly
what you'd expect from a real evaluation loop, not a rounding artifact of a
fake test. Prometheus's own `/api/v1/rules` at the moment of firing:

```json
{
  "state": "firing",
  "name": "DatacernHighErrorRate",
  "query": "sum by (job) (rate(http_requests_total{status=~\"5..\"}[5m])) / sum by (job) (rate(http_requests_total[5m])) > 0.01",
  "duration": 300,
  "alerts": [
    {
      "labels": {"alertname": "DatacernHighErrorRate", "job": "notification-service", "severity": "critical"},
      "annotations": {"summary": "notification-service 5xx rate above 1% for 5m", ...},
      "state": "firing",
      "activeAt": "2026-07-24T00:27:09.042395687Z",
      "value": "8.275862068965517e-01"
    }
  ],
  "health": "ok"
}
```

The measured 5xx ratio at firing time was **~82.8%** (`0.8276`) — far above
the 1% threshold, because the synthetic-load generator's 1 req/s of
guaranteed-500s dominated the job's total traffic (the only other requests
against notification-service during the drill were Prometheus's own 5s
`/metrics` scrapes, all 200s). This is expected and correct for a drill: the
point was proving the rule *evaluates and transitions correctly*, not
calibrating a realistic incident magnitude.

**Post-drill teardown/restore — verified, not assumed:**
- `drill.sh`'s `trap cleanup EXIT` fired: synthetic-load generator killed,
  `datacern-drill-prom` container removed (`docker ps --filter
  name=datacern-drill-prom` empty afterward), notification-service stopped
  and restarted a second time with `CHAOS_ENDPOINTS_ENABLED=false`.
- Confirmed directly after the run completed (not just trusting the script's
  own log): `GET /healthz` → `200`; `POST /internal/chaos/error` → `404`
  (back to "does not exist" — chaos mode is off); the live process is a new
  PID (71901) matching `deploy/e2e/run/pids/notification.pid`, i.e. a real
  restart happened, not a stale process still answering.
- `drill.sh` itself exited (confirmed no longer in the process table); its
  own final log line was `ok PASS -- DatacernHighErrorRate reached firing`.

## Verdict: **PASS**

`DatacernHighErrorRate` — previously verified only via `helm lint`/`helm
template` — has now been proven to genuinely transition
`inactive -> pending -> firing` against a live Prometheus rule-evaluation
engine scraping a real service's real metrics, closing the "alerts wired but
never fired in a real incident" gap for this rule. The other 9 rules in the
bundle share the same `groups:`-extraction and scrape-config mechanism;
re-running this drill's pattern against them (different synthetic triggers
per rule — e.g. sustained in-flight requests for
`DatacernHighInFlightRequests`, artificial latency for the p95 rules) is a
mechanical follow-up, not a new design, and is explicitly left as future work
rather than claimed done here.
