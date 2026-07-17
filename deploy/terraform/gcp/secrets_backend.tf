# Optional APP-level secrets path (BYO Infra Hardening Phase 2,
# docs/design/byo-infra-hardening.md): when a deployment sets
# SECRETS_BACKEND=gcp, ingestion-service's GCPSecretManagerStore and
# identity-service's GCPKMSSigner need (a) Secret Manager access namespaced
# under an app-secrets prefix and (b) a pre-existing Cloud KMS key ring — Key
# Rings can't be created dynamically by the app (they're also undeletable, so
# Windrose deliberately never creates one per boot; `gcpkms.Signer` assumes
# one already exists, provisioned here).
#
# Off by default (`enable_app_secrets_backend = false`) and a no-op unless
# `secrets_backend = "gcp"` — this file adds nothing to a Vault-backed (the
# default) or non-GCP deployment. Distinct from secretmanager.tf's single
# `windrose-secrets` blob, which only holds INFRA credentials synced by
# External Secrets Operator.

variable "secrets_backend" {
  description = "Which SECRETS_BACKEND the workloads are configured with (vault|aws|azure|gcp). Only used to gate the optional resources in this file — does not itself configure any service."
  type        = string
  default     = "vault"

  validation {
    condition     = contains(["vault", "aws", "azure", "gcp"], var.secrets_backend)
    error_message = "secrets_backend must be one of: vault, aws, azure, gcp."
  }
}

variable "enable_app_secrets_backend" {
  description = "Provision the GCP Secret Manager IAM grant + Cloud KMS key ring for SECRETS_BACKEND=gcp (stretch goal of BYO Infra Hardening Phase 2). Off by default; a no-op unless secrets_backend = \"gcp\"."
  type        = bool
  default     = false
}

variable "app_secrets_backend_ksas" {
  description = "K8s ServiceAccount names (in k8s_namespace) that need the app-secrets/signing GSA via Workload Identity when the app secrets backend is enabled."
  type        = list(string)
  default     = ["ingestion-service", "identity-service"]
}

locals {
  app_secrets_backend_enabled = var.enable_app_secrets_backend && var.secrets_backend == "gcp"
  app_secrets_id_prefix       = "${var.name_prefix}-app-"
}

resource "google_service_account" "app_secrets" {
  count        = local.app_secrets_backend_enabled ? 1 : 0
  account_id   = "${var.name_prefix}-app-secrets"
  display_name = "Windrose app secrets/signing (SECRETS_BACKEND=gcp)"
}

resource "google_service_account_iam_member" "app_secrets_wi" {
  for_each           = local.app_secrets_backend_enabled ? toset(var.app_secrets_backend_ksas) : toset([])
  service_account_id = google_service_account.app_secrets[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${local.wi_pool}[${var.k8s_namespace}/${each.value}]"
}

# ---------------------------------------------------------------------------
# Secret Manager: conditional binding scoped to the app-secrets id prefix
# (GCPSecretManagerStore._sanitize_name always emits "wr-..." names; the
# condition below is intentionally slightly broader — name prefix per
# name_prefix — so it also covers the folded embed-secret hash).
# ---------------------------------------------------------------------------
resource "google_project_iam_member" "app_secrets_manager" {
  count   = local.app_secrets_backend_enabled ? 1 : 0
  project = var.project_id
  role    = "roles/secretmanager.admin"
  member  = "serviceAccount:${google_service_account.app_secrets[0].email}"

  condition {
    title       = "app-secrets-prefix-only"
    description = "Only secrets named ${local.app_secrets_id_prefix}* (Windrose app secrets, not the windrose-secrets infra-creds blob)."
    expression  = "resource.name.startsWith(\"projects/${var.project_id}/secrets/${local.app_secrets_id_prefix}\")"
  }
}

# ---------------------------------------------------------------------------
# Cloud KMS: the key ring identity-service's GCPKMSSigner mints asymmetric
# signing CryptoKeys into (CryptoKeys/Versions ARE created dynamically by the
# adapter — only the Key Ring itself must pre-exist).
# ---------------------------------------------------------------------------
resource "google_kms_key_ring" "app_signing" {
  count    = local.app_secrets_backend_enabled ? 1 : 0
  name     = "${var.name_prefix}-app-signing"
  location = var.region
}

resource "google_kms_key_ring_iam_member" "app_signing_admin" {
  count       = local.app_secrets_backend_enabled ? 1 : 0
  key_ring_id = google_kms_key_ring.app_signing[0].id
  role        = "roles/cloudkms.admin"
  member      = "serviceAccount:${google_service_account.app_secrets[0].email}"
}

resource "google_kms_key_ring_iam_member" "app_signing_signer" {
  count       = local.app_secrets_backend_enabled ? 1 : 0
  key_ring_id = google_kms_key_ring.app_signing[0].id
  role        = "roles/cloudkms.signerVerifier"
  member      = "serviceAccount:${google_service_account.app_secrets[0].email}"
}

output "app_signing_key_ring" {
  description = "Full resource name of the pre-provisioned Cloud KMS key ring for GCP_KMS_KEY_RING (only set when enable_app_secrets_backend = true and secrets_backend = \"gcp\")."
  value       = local.app_secrets_backend_enabled ? google_kms_key_ring.app_signing[0].id : null
}
