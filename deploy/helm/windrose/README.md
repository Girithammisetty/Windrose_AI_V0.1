# Windrose umbrella chart

Renders one **Deployment + Service** (plus an optional **HPA** and a pre-install
**migration Job**) per microservice, from a single `services:` list in
`values.yaml` that mirrors [`deploy/services.yaml`](../../services.yaml).

Every workload consumes config the same way (see [`deploy/CONFIG.md`](../../CONFIG.md)):

- **ConfigMap `windrose-config`** — non-secret settings (`.Values.config`), via `envFrom`.
- **Secret `windrose-secrets`** — synced from the cloud secret manager (External
  Secrets Operator or the CD workflow), via `envFrom`. The chart does **not**
  create it by default (`secrets.create: false`).

## Install / upgrade

```sh
helm upgrade --install windrose deploy/helm/windrose \
  -f deploy/helm/windrose/values-aws.yaml \      # or -gcp / -azure
  --set global.imageTag=<git-sha-or-tag>
```

Lint before shipping:

```sh
helm lint deploy/helm/windrose -f deploy/helm/windrose/values-aws.yaml
helm template windrose deploy/helm/windrose -f deploy/helm/windrose/values-aws.yaml | less
```

## What each cloud file overrides

`values-<cloud>.yaml` carries **only** cloud-specific settings: registry, ingress
class/annotations (`alb` / `gce` / `azure-application-gateway`), `storageClass`,
ServiceAccount workload-identity annotations (IRSA / GKE WI / AAD), and
`externalSecrets.secretStoreRef`. Managed-infra endpoints are placeholders that
the CD workflow overrides from Terraform outputs.

## Migrations

- **Python services** (`migrate: true`) get a `pre-install,pre-upgrade` Job that
  runs `python -m alembic upgrade head` against `windrose-secrets`' DB creds.
- **Go services** self-migrate idempotently on boot (advisory-locked, under
  `MIGRATE_DATABASE_URL`), so they get no separate Job by default. To force one,
  set `migrateCommand: [...]` on that service entry in values.

## Secrets

Provide the keys in `deploy/CONFIG.md` in your cloud secret manager under
`externalSecrets.remoteKeyPrefix` (default `windrose/prod`). Enable
`externalSecrets.enabled: true` (already set in each `values-<cloud>.yaml`) to
have the operator materialize `windrose-secrets`. Nothing secret is committed.

## Internal MCP facades & network isolation

`case-service` and `chart-service` host in-cluster **MCP backend facades**
(`POST /internal/v1/mcp/invoke`) that authorize the calling peer by a
mesh-injected `X-Spiffe-Id` header (`config.CASE_FACADE_ALLOWED_SPIFFE` /
`CHART_FACADE_ALLOWED_SPIFFE`, defaulted to the `mcp-gateway` identity). The
facades **fail closed** (403) when the allowlist is unset.

That header is only trustworthy under two conditions, so ship both:

- **mTLS between pods** (a service mesh / SPIRE) so the *mesh* — not an arbitrary
  client — sets `X-Spiffe-Id`. The chart does **not** provide this; it is an
  infra-layer prerequisite.
- **Ingress pinned to in-cluster peers.** The chart renders one default-deny
  `NetworkPolicy` per facade service (`networkPolicies.enabled`, default **true**)
  allowing ingress on the service port only from other windrose pods. This is the
  **minimum** L4 isolation, not a substitute for the mesh mTLS above.

If your Prometheus/monitoring stack scrapes `/metrics` from another namespace, add
its namespace to `networkPolicies.extraNamespaceSelectors` (metrics share the
service port).

## Adding a service

Add it to `deploy/services.yaml` (source of truth for CI) **and** the `services:`
list here. CI derives its build/test matrix from `deploy/services.yaml`; the chart
ranges over `.Values.services`.
