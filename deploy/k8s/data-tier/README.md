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
| Trino (large-query engine) | `trino:8080` | Deployment | — (catalog ConfigMap) |

`+` two bootstrap Jobs (`minio-createbuckets`, `mlflow-createdb`) and the
`clickhouse-config` / `trino-catalog` ConfigMaps.

> **Optional add-ons** live in `optional-vault-mailpit.yaml` (kept out of the
> kustomization so `apply -k` stays lean) — Vault (dev-mode BYO-secrets backend)
> and Mailpit (SMTP capture, UI on `:8025`):
> ```bash
> kubectl apply -f deploy/k8s/data-tier/optional-vault-mailpit.yaml
> # then wire them into the secret:
> VAULT_ADDR=http://vault:8200 VAULT_TOKEN=windrose_dev_root \
>   SMTP_HOST=mailpit SMTP_PORT=1025 ./create-secrets.sh
> ```
> otel-collector and temporal-ui from the compose file are still omitted — add
> them the same way if you need in-cluster tracing or the Temporal web UI.

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

# 5) Create windrose-secrets (compose dev defaults, pointed at this data tier).
#    Idempotent; override any value via env (PG_PASSWORD=... OBJ_SECRET=... etc.).
./create-secrets.sh
```

Then install the app chart with `values-hetzner.yaml` (see
`deploy/terraform/hetzner/README.md`).

### About `windrose-secrets` (`create-secrets.sh`)
Builds the Secret from `deploy/CONFIG.md`'s key contract with the in-cluster
data-tier values. **Auth is dynamic:** `JWKS_URL` in `values-hetzner.yaml` points
verifiers at identity-service's live JWKS (its dev signer self-generates the
keypair each boot), so `JWT_SIGNING_KEY_PEM` / `JWT_JWKS` are deliberately *not*
in the dev secret — those belong to the production KMS/ESO path only. For
production, don't run this script: sync `windrose-secrets` from your cloud secret
manager via External Secrets with real, rotated values.

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
