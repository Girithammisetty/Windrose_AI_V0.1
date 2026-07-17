# iam.tf — Workload Identity: two Google service accounts bound to the K8s SAs
# that the Helm chart uses.
#
#   1. external-secrets GSA — Secret Manager accessor, bound to the ESO KSA.
#      Its email goes on the ESO ServiceAccount annotation
#      (iam.gke.io/gcp-service-account).
#   2. storage GSA — object admin on the four Windrose buckets, bound to each
#      workload KSA that touches GCS. Its email goes on those KSAs' annotations.
#
# No SA keys are ever created; access is entirely via Workload Identity.

locals {
  wi_pool = "${var.project_id}.svc.id.goog"
}

########################################
# External Secrets Operator GSA
########################################

resource "google_service_account" "external_secrets" {
  account_id   = "${var.name_prefix}-eso"
  display_name = "Windrose External Secrets accessor"
}

# Read-only access to the windrose-secrets secret (scoped to the one secret).
resource "google_secret_manager_secret_iam_member" "eso_accessor" {
  secret_id = google_secret_manager_secret.windrose.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.external_secrets.email}"
}

# Bind the ESO KSA -> ESO GSA (Workload Identity).
resource "google_service_account_iam_member" "eso_wi" {
  service_account_id = google_service_account.external_secrets.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${local.wi_pool}[${var.external_secrets_namespace}/${var.external_secrets_ksa}]"
}

########################################
# GCS workload GSA
########################################

resource "google_service_account" "storage" {
  account_id   = "${var.name_prefix}-storage"
  display_name = "Windrose GCS workload identity"
}

# Object admin scoped to each bucket (not project-wide).
resource "google_storage_bucket_iam_member" "storage_object_admin" {
  for_each = google_storage_bucket.this
  bucket   = each.value.name
  role     = "roles/storage.objectAdmin"
  member   = "serviceAccount:${google_service_account.storage.email}"
}

# Bind each workload KSA (in k8s_namespace) -> storage GSA (Workload Identity).
resource "google_service_account_iam_member" "storage_wi" {
  for_each           = toset(var.gcs_workload_ksas)
  service_account_id = google_service_account.storage.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${local.wi_pool}[${var.k8s_namespace}/${each.value}]"
}

########################################
# Managed Kafka access for GCS workload GSA (OAUTHBEARER via IAM)
########################################

# When using Managed Kafka, workloads authenticate as their GSA. Grant the
# storage GSA the Kafka client role so the same identity can also produce/consume.
resource "google_project_iam_member" "kafka_client" {
  count   = var.kafka_backend == "managed_kafka" ? 1 : 0
  project = var.project_id
  role    = "roles/managedkafka.client"
  member  = "serviceAccount:${google_service_account.storage.email}"
}

# When using the Pub/Sub fallback, grant publish/subscribe instead.
resource "google_project_iam_member" "pubsub_editor" {
  count   = var.kafka_backend == "pubsub" ? 1 : 0
  project = var.project_id
  role    = "roles/pubsub.editor"
  member  = "serviceAccount:${google_service_account.storage.email}"
}
