# Windrose in-cluster data tier (self-hosted / k3s / Hetzner)

The stateful dependencies the Windrose services need, translated 1:1 from
`deploy/docker-compose.dev.yml` (same images, ports, env). Deploy this **before**
the app chart; the app's `values-hetzner.yaml` points every endpoint at these
ClusterIP DNS names.

**Scope:** dev/staging, CPU-only. Credentials are the compose defaults
(`windrose` / `windrose_dev`) inline in the manifests — fine here, **not for
production** (move to a Secret + rotate).

## Components (28 resources)
| Service | DNS:port | Kind | Storage |
|---|---|---|---|
| Postgres (pgvector) | `postgres:5432` | StatefulSet | 20Gi |
| Redis | `redis:6379` | Deployment | — |
| Redpanda (Kafka) | `redpanda:9092` (SR `:8081`) | StatefulSet | 10Gi |
| MinIO (S3) | `minio:9000` (console `:9001`) | StatefulSet | 20Gi |
| Iceberg REST | `iceberg-rest:8181` | StatefulSet | 5Gi (sqlite catalog) |
| OpenSearch | `opensearch:9200` | StatefulSet | 10Gi |
| ClickHouse | `clickhouse:8123` / `:9000` | StatefulSet | 10Gi |
| OPA | `opa:8281` | Deployment | — (policy ConfigMap) |
| Keycloak | `keycloak:8080` | Deployment | — |
| Temporal | `temporal:7233` | Deployment | — (uses Postgres) |
| MLflow | `mlflow:5000` | Deployment | — (Postgres + MinIO) |
| Ollama (CPU LLM) | `ollama:11434` | StatefulSet | 20Gi |

`+` two bootstrap Jobs (`minio-createbuckets`, `mlflow-createdb`) and the
`clickhouse-config` ConfigMap.

> Trino, Vault, otel-collector, mailpit, and temporal-ui from the compose file
> are **not** included — they're optional for the core data plane. Add them the
> same way if you need the Trino large-query engine, BYO-secrets, in-cluster
> tracing, an email sink, or the Temporal web UI.

## Apply

```bash
# 1) One manual pre-step: the OPA policy bundle. It lives in the rbac service and
#    is loaded from files, so it's created imperatively (avoids drift vs. inlining
#    the Rego). Required — authz fails without it.
kubectl create namespace windrose --dry-run=client -o yaml | kubectl apply -f -
kubectl -n windrose create configmap opa-policy \
  --from-file=services/rbac-service/policy/windrose_authz.rego \
  --from-file=services/rbac-service/policy/windrose_authz_input.rego

# 2) The data tier.
kubectl apply -k deploy/k8s/data-tier

# 3) Watch it come up.
kubectl -n windrose get pods -w

# 4) Pull a small model for CPU Ollama.
kubectl -n windrose exec deploy/ollama -- ollama pull llama3.2:3b
```

Then create `windrose-secrets` and install the app chart with
`values-hetzner.yaml` (see `deploy/terraform/hetzner/README.md`).

## Notes
- **StorageClass** is `hcloud-volumes` throughout — change it (`sed -i
  's/hcloud-volumes/<your-sc>/'`) for a different CSI.
- **OpenSearch** runs a privileged init container to set `vm.max_map_count`, and
  `bootstrap.memory_lock=false` (avoids the unbounded-memlock ulimit requirement
  in k8s).
- **MLflow** installs `psycopg2-binary`+`boto3` at boot (same as compose), so its
  first readiness takes ~30–60s.
- **Ollama** is new here (not in compose — on Mac it runs natively on the host).

## Validate without a cluster
`kubectl kustomize deploy/k8s/data-tier` renders all 28 resources with no cluster
or spend.
