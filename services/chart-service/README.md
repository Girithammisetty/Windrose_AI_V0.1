# chart-service (Go)

Dashboards and charts for the Insights and Case Management modules (BRD 07).
Resolves chart data by compiling chart definitions through **semantic-service**
and executing through **query-service**, applies **server-side aggregation by
default**, caches shaped results in **Redis** (ETag/304 + event-driven
invalidation), and serves drilldowns and CSV/PNG exports.

## Architecture (no stubs in the runtime)

Every adapter is real and wired by default from `cmd/server` (`internal/config`):

| Port | Real adapter | Backing infra |
|---|---|---|
| Persistence | `store.PG` (pgx + RLS) | Postgres 16 |
| Result cache | `cache.Redis` | Redis 7 |
| AuthN | `authjwt.Verifier` (JWKS) | identity-service JWKS |
| AuthZ | `authz.OPA` (go-common opaclient) | OPA sidecar + Redis projection |
| Compile | `resolve.HTTPSemantic` | semantic-service `POST /compile` |
| Execute | `resolve.HTTPQuery` | query-service `POST /sql/run` + `GET /executions/{id}/results` |
| Events out | go-common outbox relay → `kafka.Producer` | Redpanda |
| Events in | go-common `ConsumerGroup` invalidation | Redpanda |
| Export store | `export.FSStore` (MinIO/S3-shaped local) | filesystem (dev) / object store |

There is **no env flag that selects a fake**. In-memory doubles exist only in
`*_test.go` and are never reachable from `cmd/server`.
`internal/config.TestAC00_BootDefaultAdaptersAreReal` boots the default-env
wiring and asserts every adapter is a real type.

### Postgres RLS + non-owner role (systemic hardening)

- Migration `000002_rls.up.sql` runs `ENABLE` + **`FORCE ROW LEVEL SECURITY`**
  on every tenant table and creates a shipped runtime role **`chart_app`**
  (`LOGIN NOSUPERUSER NOBYPASSRLS`).
- The shipped default `DATABASE_URL` connects as `chart_app` — **not** the
  migration owner (`windrose`/`postgres`). A superuser/BYPASSRLS role would
  silently ignore RLS; the runtime pool never uses one.
- `MIGRATE_DATABASE_URL` (owner) runs migrations; `DATABASE_URL` (non-owner)
  backs the request pool.
- `TestAC12_RLSCrossTenantEmpty` connects with the shipped `chart_app` role and
  proves cross-tenant reads return empty / HTTP 404.

## Run

```bash
export PATH=/opt/homebrew/bin:$PATH
# unit tier — no Docker
make test-unit
# integration tier — Docker (Testcontainers Postgres + Redis; real HTTP servers
# speaking the semantic-/query-service contracts). Auto-skips if Docker is down.
make test-integration
# boot (real infra from deploy/docker-compose.dev.yml)
MIGRATE_DATABASE_URL=postgres://windrose:windrose_dev@localhost:5432/chart?sslmode=disable \
DATABASE_URL=postgres://chart_app:chart_app@localhost:5432/chart?sslmode=disable \
REDIS_ADDR=localhost:6379 OPA_URL=http://localhost:8281 \
SEMANTIC_SERVICE_URL=http://localhost:8086 QUERY_SERVICE_URL=http://localhost:8085 \
KAFKA_BROKERS=localhost:9092 make run
```

Key env: `LISTEN_ADDR` (`:8087`), `MIGRATE_DATABASE_URL`, `DATABASE_URL`,
`REDIS_ADDR`, `OPA_URL`, `JWKS_URL`, `SEMANTIC_SERVICE_URL`,
`QUERY_SERVICE_URL`, `EXPERIMENT_SERVICE_URL`, `DATASET_SERVICE_URL`,
`KAFKA_BROKERS` (`false` disables the relay + consumers for broker-less dev),
`EXPORT_ROOT`, `EXPORT_SIGNING_SECRET`, `PNG_RENDERER_URL`, `RBAC_URL`.

## Chart-type catalog (30, CHART-FR-011)

- **Query/semantic (25):** line_chart, scatter_plot, pie_chart, funnel_chart,
  bubble_chart, gauge_chart, sunburst_chart, vertical_bar_chart,
  vertical_stackedbar_chart, sankey_chart, whisker_chart, combination_chart,
  grid_chart, geo_map_chart, tree_map_chart, heatmap_chart, histogram_chart,
  waterfall_chart, word_cloud_chart, chord_chart, decision_tree_chart,
  network_graph_chart, network_chart, tree_chart, pivot_table_chart.
- **Dataset (2):** metric_chart, parameter_chart.
- **Run (3):** roc_curve, confusion_matrix, decision_tree.

Config families: `axis` (10), `y_only` (4), `heatmap` (5), `network` (4),
`grid` (2), `metric` (5). `GET /chart-types` serves each type's JSON Schema.

## FR → code/test traceability

| FR / AC | Code | Test |
|---|---|---|
| CHART-FR-001..007 dashboards | `internal/api/handlers_dashboards.go`, `store/pg.go` | `TestAC09_DashboardNameConflict`, `TestIdempotentReplay` |
| CHART-FR-010..016 charts, sources, links, guards | `handlers_charts.go`, `handlers_link.go`, `store/pg.go` | `TestAC05_*`, `TestAC08_*`, `TestAC10_CircularLinkAndCleanup` |
| CHART-FR-011/012 catalog | `domain/charttypes.go` | `TestAC07_CatalogHas30Types`, `TestAC07_ChartTypesEndpoint` |
| CHART-FR-020..025 resolution | `resolve/*`, `handlers_data.go`, `domain/shape.go` | `TestAC01_EndToEndResolveThroughSemanticAndQuery`, `TestAC13_DeterministicSampling` |
| CHART-FR-030..033 cache/ETag | `cache/cache.go`, `handlers_data.go` | `TestAC02_RedisCacheHit`, `TestAC03_ETag304`, `TestKeyDeterminism` |
| CHART-FR-031 invalidation | `events/consumers.go`, `cache/cache.go` | `TestAC04_EventDrivenInvalidation` |
| CHART-FR-040 drilldown | `resolve/resolver.go`, `handlers_data.go` | `TestAC06_DrilldownThroughQueryService`, `TestNoDrilldownConfigured` |
| CHART-FR-041 export | `export/export.go`, `handlers_export.go` | `TestAC11_CSVExport` |
| CHART-FR-005 bundle | `handlers_bundle.go` | `TestAC14_BundleExportImportRemap` |
| MASTER isolation (AC-12) | `store/pg.go` RLS + `migrations/000002` | `TestAC12_RLSCrossTenantEmpty`, `TestAC12Unit_CrossTenantIsNotFound` |
| MASTER authz (§2.8) | `authz/opa.go`, `middleware.go` | `TestAC_OPAAuthzRealSidecar`, `TestAuthzDenyMatrix` |
| Real-by-default boot | `config/config.go` | `TestAC00_BootDefaultAdaptersAreReal` |

## Documented infra-gated exception

**PNG export** requires a headless-browser renderer sidecar. When
`PNG_RENDERER_URL` is unset, a PNG export operation completes as `failed` with
`PNG_RENDERER_UNAVAILABLE` — never a fake image. **CSV export is fully real**
(RFC 4180 + UTF-8 BOM, streamed to the object store behind a 15-min
HMAC-signed URL). This is the only deferred adapter.
