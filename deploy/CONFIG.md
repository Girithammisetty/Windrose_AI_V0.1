# Windrose — configuration & credentials contract

Everything a running cluster needs is **referenced, never hard-coded**. Non-secret
settings come from a ConfigMap (`windrose-config`); secrets come from a single K8s
Secret (`windrose-secrets`) that is **synced from the cloud's secret manager** — so
credentials are filled in *later*, in the cloud console / Terraform vars, and rotated
without touching images or manifests.

```
 cloud secret manager (AWS Secrets Manager | GCP Secret Manager | Azure Key Vault)
        │  (External Secrets Operator, or the CD workflow at deploy time)
        ▼
 K8s Secret  windrose-secrets   ──►  every service Deployment  (envFrom)
 K8s ConfigMap windrose-config  ──►  every service Deployment  (envFrom)
```

## What you fill in later (secrets)

Provide these keys in your cloud secret manager under the path in `values-<cloud>.yaml`
(`secrets.remoteRef`). None have defaults; the platform fails closed without them.

| Secret key | Used by | Notes |
|---|---|---|
| `POSTGRES_HOST` `POSTGRES_PORT` `POSTGRES_ADMIN_USER` `POSTGRES_ADMIN_PASSWORD` | all stateful services + migration jobs | admin/DDL role; Terraform outputs the managed-DB endpoint |
| `POSTGRES_APP_PASSWORD_<DB>` | each service's runtime role | one non-superuser, `NOBYPASSRLS` role per DB (RLS isolation) |
| `REDIS_URL` | cache/dedup/rate-limit (Python services) | `rediss://user:pass@host:port/0` — scheme-driven auth/TLS, no other var needed |
| `REDIS_ADDR` `REDIS_USERNAME` `REDIS_PASSWORD` `REDIS_TLS` | cache/dedup/rate-limit (Go services) | `REDIS_ADDR` is bare `host:port`; the other three are optional — set `REDIS_TLS=true` for managed Redis (ElastiCache/Azure Cache/Memorystore); omit all three for local unauthenticated Redis/Valkey |
| `KAFKA_BOOTSTRAP` `KAFKA_SASL_MECHANISM` `KAFKA_SASL_USERNAME` `KAFKA_SASL_PASSWORD` `KAFKA_TLS` | event bus | MSK/Managed-Kafka/Event-Hubs (Kafka API). `KAFKA_SASL_MECHANISM` ∈ `plain` \| `scram-sha-256` \| `scram-sha-512` (MSK: SCRAM; Confluent Cloud: PLAIN with an API key/secret; Event Hubs: PLAIN with username `$ConnectionString`); omit for local unauthenticated Kafka/Redpanda. Set `KAFKA_TLS=true` alongside SASL for every managed offering. GCP Managed Service for Kafka's SASL/OAUTHBEARER (IAM token refresh) is not yet supported — use SCRAM or Pub/Sub's Kafka-compat shim instead |
| `OBJECTSTORE_ENDPOINT` `OBJECTSTORE_ACCESS_KEY` `OBJECTSTORE_SECRET_KEY` `OBJECTSTORE_REGION` | ingestion/dataset/query/pipeline/inference | S3/GCS(interop)/Blob; prefer workload identity (IRSA / Workload Identity / AAD Workload Identity) over static keys |
| `JWT_SIGNING_KEY_PEM` `JWT_JWKS` | identity + every verifier | RS256 signing key + published JWKS |
| `KEYCLOAK_ADMIN_USER` `KEYCLOAK_ADMIN_PASSWORD` | identity provisioning | or point `OIDC_ISSUER` at your own IdP |
| `VAULT_ADDR` `VAULT_TOKEN` | ingestion (connector secrets) | or swap for the cloud secret store directly |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASSWORD` | notification | transactional email |
| `OPENAI_API_KEY` `ANTHROPIC_API_KEY` `AZURE_OPENAI_*` | ai-gateway (optional providers) | omit to run Ollama-only / Bedrock / Vertex via workload identity |
| `CLICKHOUSE_URL` `CLICKHOUSE_PASSWORD` | audit + usage | metering/audit sink |

## Non-secret config (ConfigMap, set per cloud)

`ICEBERG_CATALOG_URI`, `ICEBERG_WAREHOUSE`, `OPA_URL`, `MLFLOW_TRACKING_URI`,
`TEMPORAL_HOST`, `OPENSEARCH_URL`, `OLLAMA_BASE_URL`, `JWT_ISSUER`, `JWT_AUDIENCE`,
`WINDROSE_ENV=production`, `WINDROSE_OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`
(see "Observability" below), and the in-cluster service DNS names (e.g.
`RBAC_URL=http://rbac-service:8302`). These are set from `values-<cloud>.yaml`.

## Observability — bring your own backend

Windrose does not ship an observability backend; it exports to **yours**
(Datadog, Honeycomb, Grafana Cloud, New Relic, Splunk Observability, or a
self-hosted OTel Collector / Jaeger / Tempo). Every service already contains
the real exporter code (`libs/go-common/otelx`, `libs/py-common/windrose_common/
otelx.py`); this is a Helm/config wiring problem, not a code problem.

### Traces (OTLP)

Set two ConfigMap values (`deploy/helm/windrose/values.yaml` → `config:`, or
override per cloud in `values-<cloud>.yaml`):

| Key | Default | Effect |
|---|---|---|
| `WINDROSE_OTEL_ENABLED` | `"false"` | Set `"true"` to install the tracer provider. A non-empty `OTEL_EXPORTER_OTLP_ENDPOINT` also implicitly enables it. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset (commented out) | `host:port` of your collector/vendor endpoint, e.g. `otel-collector.observability.svc:4317`. |

Both are wired but unset by default — every service's tracer provider is a
true no-op until you set them (verified: unset `WINDROSE_OTEL_ENABLED` and
empty endpoint mean `Enabled()`/`configure_tracing()` both return early with
zero exporter construction, so this is genuinely opt-in, not "on but pointed
nowhere").

**Protocol — read carefully, this differs from some vendor docs**: as of this
writing, **both** Go and Python services export via **OTLP/gRPC only**:

- Go: `go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc`
  (`libs/go-common/otelx/otelx.go`).
- Python: `opentelemetry-exporter-otlp-proto-grpc`'s `OTLPSpanExporter`
  (`libs/py-common/windrose_common/otelx.py`) — **not** the HTTP exporter,
  despite OTel's Python SDK supporting both. There is currently no
  `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` switch wired in either language.

Point `OTEL_EXPORTER_OTLP_ENDPOINT` at whatever your collector/vendor exposes
on its **gRPC** OTLP port (commonly `4317`), not its HTTP port (commonly
`4318`). Most vendors' own OTel Collector distributions, and most vendor
Agents, accept OTLP/gRPC — but if your collector is HTTP-only, traces will not
arrive; this is a real gap to close in a follow-up (add an HTTP exporter
variant / `OTEL_EXPORTER_OTLP_PROTOCOL` switch to `otelx.py`), not something
to work around by misconfiguring the endpoint.

### Metrics — stays pull-based

Metrics are **not** part of this OTLP wiring. Every service already exposes a
Prometheus-format `/metrics` endpoint (`go-common/metricsx`, `py-common/
windrose_common/metricsx.py` — dependency-free RED registries, no
`prometheus_client`/vendor SDK needed). Nothing changes here; you have two
ways to scrape it:

1. **Prometheus Operator customers (recommended)**: set
   `observability.serviceMonitor.enabled: true` in your values file. This
   renders a `ServiceMonitor` (`monitoring.coreos.com/v1`) per service in
   `deploy/services.yaml`'s inventory, scraping `/metrics` on the existing
   `http` port every 30s. Requires the Prometheus Operator CRDs to already be
   installed in-cluster; the template renders nothing when the flag is
   `false` (the default), so charts without the CRD are unaffected. Set
   `observability.serviceMonitor.labels` if your Prometheus's
   `serviceMonitorSelector` requires a specific label (e.g. `release:
   kube-prometheus-stack`).
2. **No Prometheus Operator**: hand-write a `scrape_configs` entry against
   Kubernetes service discovery, pointed at each Service's `http` port,
   `/metrics` path — the same target ServiceMonitor would generate, just
   configured directly in your Prometheus/vendor-agent's own config.

### Logs

Go services already emit structured JSON to stdout
(`slog.NewJSONHandler(os.Stdout, nil)`, tagged `MASTER-FR-050`) — pipe stdout
to your log forwarder (Fluent Bit, Vector, Datadog Agent, CloudWatch/Cloud
Logging/Azure Monitor's node-level collector) as-is.

Python services now have the equivalent: `windrose_common.logging.configure_json_logging()`
installs a dependency-free JSON `logging.Formatter` on the root logger
(`libs/py-common/windrose_common/logging.py`), so Python stdout is
forwarder-friendly the same way. As of this phase it is wired into
`eval-service`, `ai-gateway`, and `agent-runtime` as the proof of pattern;
remaining Python services should add the same one-line call
(`configure_json_logging("<service-name>")`, first thing in `app/main.py`,
mirroring where Go's `main()` calls `slog.SetDefault`) at their next touch —
this is now the standard, not a one-off.

## Registry & image config (CI)

CI publishes images to a configurable registry. Set repo/org **variables** (not secrets
unless the registry needs a password):

| CI variable | Example |
|---|---|
| `REGISTRY` | `ghcr.io/acme` · `123456789.dkr.ecr.us-east-1.amazonaws.com` · `us-docker.pkg.dev/acme/windrose` · `acme.azurecr.io` |
| `IMAGE_TAG` | defaults to the commit SHA |

## Cloud auth for deploy (CI → cloud, keyless)

Prefer **OIDC federation** — no long-lived cloud keys in CI:

| Cloud | CI auth | Set |
|---|---|---|
| AWS | `aws-actions/configure-aws-credentials` (OIDC) | `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `EKS_CLUSTER` |
| GCP | `google-github-actions/auth` (Workload Identity Federation) | `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`, `GKE_CLUSTER`, `GCP_REGION` |
| Azure | `azure/login` (OIDC) | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AKS_CLUSTER`, `AKS_RG` |

All of the above live in GitHub → Settings → Secrets/Variables (or per-environment).
Nothing here is baked into an image or committed.
