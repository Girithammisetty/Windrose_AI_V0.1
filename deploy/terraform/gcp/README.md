# Windrose — GCP infrastructure (Terraform)

Provisions the managed infrastructure the Windrose platform runs on, and publishes
the runtime secret payload into **Secret Manager** so the Helm chart's
`values-gcp.yaml` (via External Secrets Operator) can sync it into the cluster
Secret `windrose-secrets`. Credentials are **referenced, never hardcoded** — you
fill them in later through `var.secrets` / `TF_VAR_*` / CI.

## What it creates

| File | Resources |
|---|---|
| `network.tf` | VPC, subnet (secondary ranges for GKE pods/services), Cloud NAT, Private Service Access peering |
| `gke.tf` | Regional GKE cluster (Workload Identity, Dataplane V2, shielded nodes) + one autoscaling node pool + node GSA |
| `cloudsql.tf` | Cloud SQL for PostgreSQL (private IP), one database per service |
| `memorystore.tf` | Memorystore for Redis (private, AUTH + TLS) |
| `kafka.tf` | Managed Service for Apache Kafka (default) **or** Pub/Sub topics fallback (`kafka_backend`) |
| `gcs.tf` | Buckets: warehouse, uploads, profiles, pipelines (uniform access, versioning) |
| `secretmanager.tf` | `${name_prefix}-windrose-secrets` — JSON payload of derived endpoints + `var.secrets` |
| `iam.tf` | Workload Identity GSAs: External-Secrets (Secret Manager accessor) + storage (GCS/Kafka), bound to the Helm KSAs |
| `artifactregistry.tf` | Docker repo (guarded by `create_registry`) |

## Prerequisites

- A GCP project with billing enabled.
- Enable the required APIs (once per project):

  ```bash
  gcloud services enable \
    compute.googleapis.com container.googleapis.com \
    sqladmin.googleapis.com redis.googleapis.com \
    servicenetworking.googleapis.com secretmanager.googleapis.com \
    artifactregistry.googleapis.com managedkafka.googleapis.com \
    pubsub.googleapis.com iam.googleapis.com iamcredentials.googleapis.com \
    --project <project_id>
  ```

- Auth via ADC (no key files):

  ```bash
  gcloud auth application-default login
  ```

## Usage

```bash
cd deploy/terraform/gcp
cp terraform.tfvars.example terraform.tfvars   # set project_id, sizing
terraform init
terraform plan  -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

Provide secrets out-of-band (never commit them). Either uncomment the `secrets`
block in `terraform.tfvars`, or export:

```bash
export TF_VAR_secrets='{"JWT_SIGNING_KEY_PEM":"...","SMTP_PASSWORD":"...", ...}'
terraform apply
```

## What you fill in LATER

1. **`var.secrets`** — everything credential-shaped: per-DB `POSTGRES_APP_PASSWORD_<DB>`,
   JWT signing key + JWKS, Keycloak/SMTP/Vault/ClickHouse creds, optional LLM API
   keys. Full list in `deploy/CONFIG.md` and the commented block in
   `terraform.tfvars.example`. **Do not** set the infra-derived keys
   (`POSTGRES_HOST`, `REDIS_URL`, `KAFKA_BOOTSTRAP`, `OBJECTSTORE_*`,
   `POSTGRES_ADMIN_*`) — Terraform injects those from the created resources.
2. **Remote state backend** — uncomment the `backend "gcs"` block in `versions.tf`
   and point it at a state bucket (create it first).
3. **CI secrets/vars** — see WIF setup below.

## Per-service databases & roles

`cloudsql.tf` creates one **database** per service (from `deploy/services.yaml`
`db:` fields). It intentionally does **not** create the per-service Postgres
**roles**: the Helm migration Jobs connect as `POSTGRES_ADMIN_USER` and create one
`NOBYPASSRLS` role per DB using the `POSTGRES_APP_PASSWORD_<DB>` secret, keeping RLS
role management next to the schema. If you would rather manage roles in Terraform,
add the `cyrilgdn/postgresql` provider and `postgresql_role` resources — but then
the app passwords must be known at apply time.

## Wiring the Helm chart (`values-gcp.yaml`)

After apply, feed the outputs into the chart / ConfigMap:

```bash
terraform output -json
```

- Annotate the **External Secrets** KSA with
  `iam.gke.io/gcp-service-account = <external_secrets_gsa_email>`.
- Annotate each GCS-using KSA (see `gcs_workload_ksas`) with
  `iam.gke.io/gcp-service-account = <storage_gsa_email>`.
- Point the `ExternalSecret` remoteRef at `secret_id`
  (`${name_prefix}-windrose-secrets`), using `dataFrom.extract` so each JSON key
  becomes a key in `windrose-secrets`.
- Put non-secret endpoints in `windrose-config` (ConfigMap): bucket names from
  `gcs_buckets` (`ICEBERG_WAREHOUSE = gs://<warehouse bucket>`), etc.

## Workload Identity Federation for CI (keyless GitHub Actions)

The `cd-gcp.yml` workflow authenticates with no stored keys. One-time setup:

```bash
PROJECT_ID=<project_id>
POOL=windrose-github
PROVIDER=github
REPO=<org>/<repo>

# 1. Deploy service account for CI.
gcloud iam service-accounts create windrose-ci-deploy \
  --project "$PROJECT_ID" --display-name "Windrose CI deploy"
DEPLOY_SA="windrose-ci-deploy@${PROJECT_ID}.iam.gserviceaccount.com"

# 2. Roles it needs to run helm against GKE (+ read images).
for R in roles/container.developer roles/container.clusterViewer roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:${DEPLOY_SA}" --role "$R"
done

# 3. Workload Identity Pool + GitHub OIDC provider.
gcloud iam workload-identity-pools create "$POOL" \
  --project "$PROJECT_ID" --location global --display-name "Windrose GitHub"
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --project "$PROJECT_ID" --location global --workload-identity-pool "$POOL" \
  --display-name "GitHub OIDC" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition "assertion.repository=='${REPO}'" \
  --issuer-uri "https://token.actions.githubusercontent.com"

# 4. Let the GitHub repo impersonate the deploy SA.
POOL_ID=$(gcloud iam workload-identity-pools describe "$POOL" \
  --project "$PROJECT_ID" --location global --format 'value(name)')
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --project "$PROJECT_ID" --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${REPO}"

# 5. The provider resource name -> GitHub secret GCP_WIF_PROVIDER.
gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --project "$PROJECT_ID" --location global \
  --workload-identity-pool "$POOL" --format 'value(name)'
```

Then set in GitHub → Settings → Secrets and variables → Actions:

| Name | Kind | Value |
|---|---|---|
| `GCP_WIF_PROVIDER` | secret | provider resource name from step 5 |
| `GCP_DEPLOY_SA` | secret | `windrose-ci-deploy@<project>.iam.gserviceaccount.com` |
| `GKE_CLUSTER` | var/secret | `gke_cluster_name` output |
| `GCP_REGION` | var/secret | region |
| `GCP_PROJECT_ID` | var/secret | project id |

## Notes

- **No SA keys anywhere.** Local = ADC; CI = WIF; in-cluster = Workload Identity.
- **Managed Kafka auth** is GCP IAM over SASL/OAUTHBEARER (via Workload Identity);
  there is no static SASL user/password by default. Set `kafka_backend = "pubsub"`
  only if Managed Kafka is unavailable — that path uses a different wire protocol
  and needs a Pub/Sub adapter in the services.
- `terraform destroy` will not remove non-empty GCS buckets or the SQL instance
  unless you set `gcs_force_destroy = true` / `cloudsql_deletion_protection = false`.
```
