#!/usr/bin/env bash
# Create/update the `windrose-secrets` Secret for a self-hosted (k3s/Hetzner)
# dev/staging cluster, pointed at the in-cluster data tier (this directory).
#
# Emits the PER-SERVICE DSN keys the Helm chart's per-service env now references
# (<SVC>_DATABASE_URL / <SVC>_MIGRATE_URL, + AGENT_RUNTIME_ADMIN_URL /
# MEMORY_ADMIN_URL), plus the shared Redis/Kafka/object-store/ClickHouse/Keycloak
# keys. The per-service app-role credentials are the SAME dev literals the
# services' own migrations create (identity_app, dataset_app, ... — see
# deploy/e2e), so runtime auth matches after migrations run. Scheme per language:
# Go = postgres://  ;  Python runtime = postgresql+asyncpg://  ;  Python migrate
# (alembic, sync) = postgresql+psycopg://.
#
# Idempotent: re-running updates in place. NOT for production — there, sync
# windrose-secrets from your cloud secret manager (External Secrets) with real,
# rotated per-service DSNs (see deploy/CONFIG.md).
#
# JWT note: identity-service's dev signer self-generates its RSA keypair at boot
# and publishes JWKS at its own endpoint (values-hetzner.yaml sets JWKS_URL), so
# JWT_SIGNING_KEY_PEM / JWT_JWKS are intentionally NOT set here.
#
# Usage:
#   ./create-secrets.sh                      # namespace windrose, in-cluster defaults
#   NS=windrose ./create-secrets.sh          # override namespace
#   PG_ADMIN_PASSWORD=... OBJ_SECRET=... ./create-secrets.sh   # override any value
set -euo pipefail

NS="${NS:-windrose}"

# ---- shared values (override via env) ------------------------------------------
PG_HOST="${PG_HOST:-postgres}"
PG_PORT="${PG_PORT:-5432}"
PG_ADMIN_USER="${PG_ADMIN_USER:-windrose}"            # superuser: runs migrations, self-creates app roles
PG_ADMIN_PASSWORD="${PG_ADMIN_PASSWORD:-windrose_dev}"

REDIS_HOST="${REDIS_HOST:-redis}"                     # in-cluster, unauthenticated
KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-redpanda:9092}"

OBJ_ENDPOINT="${OBJ_ENDPOINT:-http://minio:9000}"
OBJ_ACCESS="${OBJ_ACCESS:-windrose}"
OBJ_SECRET="${OBJ_SECRET:-windrose_dev}"
OBJ_REGION="${OBJ_REGION:-us-east-1}"

KEYCLOAK_URL="${KEYCLOAK_URL:-http://keycloak:8080}"  # in-cluster Keycloak Service
KEYCLOAK_USER="${KEYCLOAK_USER:-admin}"
KEYCLOAK_PASSWORD="${KEYCLOAK_PASSWORD:-admin}"

CLICKHOUSE_USER="${CLICKHOUSE_USER:-windrose}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-windrose_dev}"

# Optional providers — empty runs Ollama-only (see deploy/CONFIG.md).
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
# Optional SMTP sink (optional-vault-mailpit.yaml, then SMTP_HOST=mailpit).
SMTP_HOST="${SMTP_HOST:-}"
SMTP_PORT="${SMTP_PORT:-1025}"
SMTP_USER="${SMTP_USER:-}"
SMTP_PASSWORD="${SMTP_PASSWORD:-}"
# Optional BYO-secrets backend (optional-vault-mailpit.yaml).
VAULT_ADDR="${VAULT_ADDR:-}"
VAULT_TOKEN="${VAULT_TOKEN:-}"

command -v kubectl >/dev/null || { echo "kubectl not found" >&2; exit 1; }
kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"

# ---- per-service DSN keys ------------------------------------------------------
# KEY | db | lang(go|py) | runtime-role | runtime-password  (dev literals that the
# services' migrations create; case_svc/realtimehub/notif_app_pw/eval_app_rt/
# semantic are the irregular ones — see deploy/e2e/boot_services.sh).
ADMIN="${PG_ADMIN_USER}:${PG_ADMIN_PASSWORD}@${PG_HOST}:${PG_PORT}"
ROWS="
IDENTITY|identity|go|identity_app|identity_app
RBAC|rbac|go|rbac_app|rbac_app
CASE|case_svc|go|case_app|case_app
REALTIME_HUB|realtimehub|go|realtime_app|realtime_app
TOOL_PLANE|tool_plane|go|toolplane_app|toolplane_app
QUERY|query|go|query_app|query_app
CHART|chart|go|chart_app|chart_app
USAGE|usage|go|usage_app|usage_app
NOTIFICATION|notification|go|notif_app|notif_app_pw
AUDIT|audit|go|audit_rw|audit_rw_dev
INGESTION|ingestion|py|ingestion_app|ingestion_app
DATASET|dataset|py|dataset_app|dataset_app
AGENT_RUNTIME|agent_runtime|py|agent_runtime_app|agent_runtime_app
MEMORY|memory|py|memory_app|memory_app
AI_GATEWAY|ai_gateway|py|ai_gateway_app|ai_gateway_app
PIPELINE|pipeline|py|pipeline_app|pipeline_app
EXPERIMENT|experiment|py|experiment_app|experiment_app
INFERENCE|inference|py|inference_app|inference_app
SEMANTIC|semantic|py|semantic|semantic
EVAL|eval|py|eval_app_rt|eval_app_dev
PACK|pack|py|pack_app|pack_app
"
DSN_ARGS=()
while IFS='|' read -r KEY DB LANG ROLE PW; do
  [ -z "${KEY:-}" ] && continue
  if [ "$LANG" = "go" ]; then
    DSN_ARGS+=( "--from-literal=${KEY}_DATABASE_URL=postgres://${ROLE}:${PW}@${PG_HOST}:${PG_PORT}/${DB}?sslmode=disable" )
    if [ "$KEY" = "AUDIT" ]; then
      # audit self-creates its DB + audit_rw role via ADMIN_DATABASE_URL -> postgres DB
      DSN_ARGS+=( "--from-literal=${KEY}_MIGRATE_URL=postgres://${ADMIN}/postgres?sslmode=disable" )
    else
      DSN_ARGS+=( "--from-literal=${KEY}_MIGRATE_URL=postgres://${ADMIN}/${DB}?sslmode=disable" )
    fi
  else
    DSN_ARGS+=( "--from-literal=${KEY}_DATABASE_URL=postgresql+asyncpg://${ROLE}:${PW}@${PG_HOST}:${PG_PORT}/${DB}" )
    DSN_ARGS+=( "--from-literal=${KEY}_MIGRATE_URL=postgresql+psycopg://${ADMIN}/${DB}" )
  fi
done <<EOF
$ROWS
EOF
# memory + agent-runtime open a windrose ASYNC admin pool (asyncpg, distinct scheme
# from the sync psycopg migrate DSN) for runtime CREATE SCHEMA.
DSN_ARGS+=( "--from-literal=AGENT_RUNTIME_ADMIN_URL=postgresql+asyncpg://${ADMIN}/agent_runtime" )
DSN_ARGS+=( "--from-literal=MEMORY_ADMIN_URL=postgresql+asyncpg://${ADMIN}/memory" )

# ---- build + apply (idempotent, values never printed) --------------------------
kubectl create secret generic windrose-secrets -n "$NS" \
  --from-literal=REDIS_URL="redis://${REDIS_HOST}:6379/0" \
  --from-literal=REDIS_ADDR="${REDIS_HOST}:6379" \
  --from-literal=KAFKA_BOOTSTRAP="$KAFKA_BOOTSTRAP" \
  --from-literal=KAFKA_BROKERS="$KAFKA_BOOTSTRAP" \
  --from-literal=OBJECTSTORE_ENDPOINT="$OBJ_ENDPOINT" \
  --from-literal=OBJECTSTORE_ACCESS_KEY="$OBJ_ACCESS" \
  --from-literal=OBJECTSTORE_SECRET_KEY="$OBJ_SECRET" \
  --from-literal=OBJECTSTORE_REGION="$OBJ_REGION" \
  --from-literal=AWS_ACCESS_KEY_ID="$OBJ_ACCESS" \
  --from-literal=AWS_SECRET_ACCESS_KEY="$OBJ_SECRET" \
  --from-literal=KEYCLOAK_URL="$KEYCLOAK_URL" \
  --from-literal=KEYCLOAK_ADMIN_USER="$KEYCLOAK_USER" \
  --from-literal=KEYCLOAK_ADMIN_PASSWORD="$KEYCLOAK_PASSWORD" \
  --from-literal=CLICKHOUSE_URL="http://clickhouse:8123" \
  --from-literal=CLICKHOUSE_ADDR="clickhouse:9000" \
  --from-literal=CLICKHOUSE_USER="$CLICKHOUSE_USER" \
  --from-literal=CLICKHOUSE_PASSWORD="$CLICKHOUSE_PASSWORD" \
  --from-literal=OPENAI_API_KEY="$OPENAI_API_KEY" \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  --from-literal=SMTP_HOST="$SMTP_HOST" \
  --from-literal=SMTP_PORT="$SMTP_PORT" \
  --from-literal=SMTP_USER="$SMTP_USER" \
  --from-literal=SMTP_PASSWORD="$SMTP_PASSWORD" \
  --from-literal=VAULT_ADDR="$VAULT_ADDR" \
  --from-literal=VAULT_TOKEN="$VAULT_TOKEN" \
  "${DSN_ARGS[@]}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "windrose-secrets applied to namespace '$NS' ($(kubectl get secret windrose-secrets -n "$NS" -o jsonpath='{.data}' | tr ',' '\n' | grep -c ':') keys). Values not printed."
echo "Per-service DSNs use the dev app-role creds the migrations create; auth uses JWKS_URL (identity's live endpoint)."
