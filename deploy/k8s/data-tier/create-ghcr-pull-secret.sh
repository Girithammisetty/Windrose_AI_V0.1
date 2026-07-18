#!/usr/bin/env bash
# Create the `ghcr-pull` image-pull secret so a self-hosted (k3s/Hetzner) cluster
# can pull Windrose's PRIVATE GitHub Container Registry packages. The hyperscaler
# targets pull keylessly via cloud workload identity; Hetzner has none, so pods
# authenticate to ghcr.io with this docker-registry secret (wired into
# values-hetzner.yaml as global.imagePullSecrets: [{name: ghcr-pull}]).
#
# Idempotent (create --dry-run | apply). The token is read from the environment
# and never printed.
#
# Prereq: a GitHub Personal Access Token (classic) with the `read:packages`
# scope, or a fine-grained token with Packages: read. Do NOT paste it on the
# command line where it lands in shell history — export it first:
#   export GHCR_TOKEN=ghp_xxx          # read:packages
#   export GHCR_USERNAME=your-gh-user  # the GitHub username/org that owns the PAT
#   ./create-ghcr-pull-secret.sh
set -euo pipefail

NS="${NS:-windrose}"
: "${GHCR_USERNAME:?set GHCR_USERNAME to your GitHub username}"
: "${GHCR_TOKEN:?set GHCR_TOKEN to a PAT with read:packages (export it; do not inline it)}"
GHCR_EMAIL="${GHCR_EMAIL:-noreply@windrose.ai}"

command -v kubectl >/dev/null || { echo "kubectl not found" >&2; exit 1; }
kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"

kubectl create secret docker-registry ghcr-pull -n "$NS" \
  --docker-server=ghcr.io \
  --docker-username="$GHCR_USERNAME" \
  --docker-password="$GHCR_TOKEN" \
  --docker-email="$GHCR_EMAIL" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "ghcr-pull secret applied to namespace '$NS' (token not printed)."
echo "Referenced by values-hetzner.yaml -> global.imagePullSecrets: [{name: ghcr-pull}]."
