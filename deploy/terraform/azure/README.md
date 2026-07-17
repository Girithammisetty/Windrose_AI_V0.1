# Windrose — Azure infrastructure (Terraform)

Production-grade, runnable Terraform that provisions the **managed** infrastructure
for the Windrose platform on Microsoft Azure and stages every credential in **Azure
Key Vault** so the Helm chart's External-Secrets reference (`values-azure.yaml`) can
sync them into the in-cluster `windrose-secrets` Secret.

Nothing here is hardcoded: cloud auth comes from the environment (OIDC / `az login`),
and application secrets are variables / generated values / Key-Vault entries you fill
in **later**.

## What it creates

| File | Resources |
|---|---|
| `resourcegroup.tf` | Resource group |
| `network.tf` | VNet + 3 subnets (AKS, delegated Postgres, private-endpoints) |
| `aks.tf` | AKS with **OIDC issuer + Workload Identity**, autoscaling system pool, Log Analytics |
| `postgres.tf` | PostgreSQL **Flexible Server** (private/VNet), TLS-enforced, one DB per service |
| `redis.tf` | Azure Cache for Redis, TLS-only, **private endpoint** + private DNS |
| `eventhubs.tf` | Event Hubs namespace (**Kafka endpoint :9093**) + one hub per topic + SAS rule |
| `storage.tf` | Storage account + blob containers `warehouse` `uploads` `profiles` `pipelines` |
| `keyvault.tf` | Key Vault + all app/endpoint secrets |
| `identity.tf` | User-assigned identities + **federated credentials** for ESO and Blob access |
| `acr.tf` | Optional ACR (`create_acr`) + AcrPull for AKS |

The per-service databases match the `db:` names in `deploy/services.yaml`; the Kafka
topics match the platform `*.events.v1` topics. Both are overridable variables.

## Prerequisites

- Terraform `>= 1.6`, azurerm `~> 3.116`.
- An Azure subscription and rights to create the above + role assignments.
- Auth via **one** of:
  - `az login` (local), or
  - OIDC / Workload Identity Federation in CI (`ARM_USE_OIDC=true`, `ARM_CLIENT_ID`,
    `ARM_TENANT_ID`, `ARM_SUBSCRIPTION_ID`).

## Usage

```bash
cd deploy/terraform/azure
cp terraform.tfvars.example terraform.tfvars   # edit subscription_id, sizes, etc.

terraform init                 # add a backend block (see versions.tf) for shared state
terraform plan  -out tf.plan
terraform apply tf.plan
```

Validate-only (no cloud calls):

```bash
terraform fmt -recursive .
terraform init -backend=false
terraform validate
```

## What you fill in LATER

1. **`subscription_id`** and sizing in `terraform.tfvars`.
2. **Application secrets** — the `secrets` map (see `terraform.tfvars.example` and the
   table in `deploy/CONFIG.md`): `JWT_SIGNING_KEY_PEM`, `JWT_JWKS`, `KEYCLOAK_ADMIN_*`,
   `SMTP_*`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CLICKHOUSE_*`, `VAULT_*`, etc.
   Provide them via `TF_VAR_secrets` (CI) or a git-ignored `secrets.auto.tfvars`.
   - **Not required**: `POSTGRES_HOST/PORT/ADMIN_USER`, `REDIS_URL`, `KAFKA_*`,
     `OBJECTSTORE_*` — Terraform computes these from the provisioned resources.
   - `POSTGRES_ADMIN_PASSWORD` is auto-generated if you leave it empty.
3. **Remote state backend** — uncomment/add the `azurerm` backend in `versions.tf`.

## Key Vault secret naming (important)

Key Vault forbids `_` in secret names, so each `UPPER_SNAKE` env key is stored
**hyphenated + lowercased** (`POSTGRES_HOST` → `postgres-host`). The mapping is emitted
as the `key_vault_secret_name_map` output; the Helm `values-azure.yaml` ExternalSecret
`remoteRef.key`s must use the hyphenated names while the resulting `windrose-secrets`
keys stay `UPPER_SNAKE`.

## Workload Identity wiring (outputs → serviceAccount annotations)

Annotate the ServiceAccounts so pods federate to the managed identities:

```yaml
# External Secrets Operator SA (namespace: external-secrets)
azure.workload.identity/client-id: <output external_secrets_identity_client_id>

# Blob-using service SAs (namespace: windrose)
azure.workload.identity/client-id: <output blob_identity_client_id>
```

and set `azure.workload.identity/use: "true"` on the pods. The federated credential
**subjects** are `system:serviceaccount:<ns>:<sa>` — adjust `external_secrets_*`,
`workload_namespace`, and `blob_service_accounts` variables if your SA names differ.

## OIDC federation for CI (one-time, outside this module)

Create an app registration / user-assigned identity for GitHub Actions and add a
federated credential for your repo, then set the CI secrets
`AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID`:

```bash
# Example with a user-assigned identity used by the CD workflow:
az identity create -g <rg> -n windrose-gha
az identity federated-credential create \
  --identity-name windrose-gha -g <rg> \
  --name github-main \
  --issuer https://token.actions.githubusercontent.com \
  --subject repo:<org>/<repo>:ref:refs/heads/main \
  --audiences api://AzureADTokenExchange
# Grant it Contributor (infra) / the roles the workflow needs, then wire the client id.
```

The `.github/workflows/cd-azure.yml` workflow consumes those to `azure/login` keyless,
pull AKS credentials, install External Secrets, and `helm upgrade --install`.

## Notes / trade-offs

- **Redis** uses Private Link (private endpoint) so it works on the Standard tier;
  switch `redis_sku_name = "Premium"` for VNet injection / higher SLAs.
- **Event Hubs** Kafka SASL: username is the literal `$ConnectionString`, password is
  the namespace SAS connection string (`KAFKA_SASL_PASSWORD` in Key Vault).
- **Storage** keeps `shared_access_key_enabled = true` as a static fallback for
  S3-compatible tooling; prefer the Blob Workload Identity path (`blob_identity_client_id`).
- Key Vault uses **access policies** (deployer = manage, ESO identity = get/list) to
  avoid RBAC role-propagation delays when writing secrets during `apply`.
