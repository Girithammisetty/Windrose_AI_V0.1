#!/usr/bin/env bash
# Create/update the `windrose-secrets` Secret for a self-hosted (k3s/Hetzner)
# dev/staging cluster, pointed at the in-cluster data tier (this directory).
#
# Idempotent: re-running updates in place (create --dry-run | apply). Values are
# the compose dev defaults (windrose / windrose_dev) — the same credentials the
# data-tier manifests boot with. NOT for production: there, sync windrose-secrets
# from your cloud secret manager (External Secrets) with real, rotated values —
# see deploy/CONFIG.md.
#
# JWT note (important): identity-service's dev signer (LocalSigner) GENERATES its
# own RSA keypair at boot and publishes JWKS at its own endpoint; verifiers fetch
# it live via JWKS_URL (set in values-hetzner.yaml), NOT from a static secret. So
# JWT_SIGNING_KEY_PEM / JWT_JWKS are intentionally NOT set here — they belong to
# the production KMS/ESO path only. Baking a static JWKS here would break auth
# (its kid would never match the per-boot signing key).
#
# Usage:
#   ./create-secrets.sh                 # namespace windrose, compose defaults
#   NS=windrose ./create-secrets.sh     # override namespace
#   Override any value via env, e.g.  PG_PASSWORD=... OBJ_SECRET=... ./create-secrets.sh
set -euo pipefail

NS="${NS:-windrose}"

# ---- values (override via env) --------------------------------------------------
PG_HOST="${PG_HOST:-postgres}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-windrose}"
PG_PASSWORD="${PG_PASSWORD:-windrose_dev}"

REDIS_HOST="${REDIS_HOST:-redis}"          # in-cluster, unauthenticated
KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-redpanda:9092}"

OBJ_ENDPOINT="${OBJ_ENDPOINT:-http://minio:9000}"
OBJ_ACCESS="${OBJ_ACCESS:-windrose}"
OBJ_SECRET="${OBJ_SECRET:-windrose_dev}"
OBJ_REGION="${OBJ_REGION:-us-east-1}"

KEYCLOAK_USER="${KEYCLOAK_USER:-admin}"
KEYCLOAK_PASSWORD="${KEYCLOAK_PASSWORD:-admin}"

CLICKHOUSE_USER="${CLICKHOUSE_USER:-windrose}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-windrose_dev}"

# Optional providers — empty runs Ollama-only (see deploy/CONFIG.md).
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
# Optional SMTP sink (deploy mailpit via optional-vault-mailpit.yaml, then set
# SMTP_HOST=mailpit to capture mail).
SMTP_HOST="${SMTP_HOST:-}"
SMTP_PORT="${SMTP_PORT:-1025}"
SMTP_USER="${SMTP_USER:-}"
SMTP_PASSWORD="${SMTP_PASSWORD:-}"

# Optional BYO-secrets backend (deploy vault via optional-vault-mailpit.yaml,
# then set VAULT_ADDR=http://vault:8200 VAULT_TOKEN=windrose_dev_root). Left empty
# by default so services don't try to reach a Vault that isn't deployed.
VAULT_ADDR="${VAULT_ADDR:-}"
VAULT_TOKEN="${VAULT_TOKEN:-}"

command -v kubectl >/dev/null || { echo "kubectl not found" >&2; exit 1; }
kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"

# ---- build + apply (idempotent, values never printed) --------------------------
kubectl create secret generic windrose-secrets -n "$NS" \
  --from-literal=POSTGRES_HOST="$PG_HOST" \
  --from-literal=POSTGRES_PORT="$PG_PORT" \
  --from-literal=POSTGRES_ADMIN_USER="$PG_USER" \
  --from-literal=POSTGRES_ADMIN_PASSWORD="$PG_PASSWORD" \
  --from-literal=POSTGRES_USER="$PG_USER" \
  --from-literal=POSTGRES_PASSWORD="$PG_PASSWORD" \
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
  --dry-run=client -o yaml | kubectl apply -f -

echo "windrose-secrets applied to namespace '$NS' ($(kubectl get secret windrose-secrets -n "$NS" -o jsonpath='{.data}' | tr ',' '\n' | grep -c ':') keys). Values not printed."
echo "Auth uses JWKS_URL (identity's live endpoint), set in values-hetzner.yaml — no JWT secret needed for dev."
