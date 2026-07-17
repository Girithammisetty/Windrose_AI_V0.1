# outputs.tf — everything the Helm chart / CI / values-gcp.yaml needs to wire up.

########################################
# GKE
########################################

output "gke_cluster_name" {
  description = "GKE cluster name (feed to GKE_CLUSTER in CI)."
  value       = google_container_cluster.this.name
}

output "gke_cluster_location" {
  description = "GKE cluster location/region (feed to GCP_REGION in CI)."
  value       = google_container_cluster.this.location
}

output "gke_cluster_endpoint" {
  description = "GKE control-plane endpoint."
  value       = google_container_cluster.this.endpoint
  sensitive   = true
}

########################################
# Cloud SQL
########################################

output "cloudsql_connection_name" {
  description = "Cloud SQL connection name (for the Cloud SQL Auth Proxy if used)."
  value       = google_sql_database_instance.pg.connection_name
}

output "cloudsql_private_ip" {
  description = "Cloud SQL private IP == POSTGRES_HOST."
  value       = google_sql_database_instance.pg.private_ip_address
}

output "cloudsql_databases" {
  description = "Databases created on the instance."
  value       = [for d in google_sql_database.db : d.name]
}

########################################
# Redis
########################################

output "redis_host" {
  description = "Memorystore private host."
  value       = google_redis_instance.cache.host
}

output "redis_port" {
  description = "Memorystore port."
  value       = google_redis_instance.cache.port
}

########################################
# Kafka
########################################

output "kafka_backend" {
  description = "Active event-bus backend."
  value       = var.kafka_backend
}

output "kafka_bootstrap" {
  description = "Kafka bootstrap address == KAFKA_BOOTSTRAP (empty when using the Pub/Sub fallback)."
  value       = local.kafka_bootstrap
}

output "pubsub_topics" {
  description = "Pub/Sub topics created (only when kafka_backend = pubsub)."
  value       = [for t in google_pubsub_topic.events : t.name]
}

########################################
# GCS
########################################

output "gcs_buckets" {
  description = "Map of logical bucket name => actual bucket id (feed the values-gcp ConfigMap: ICEBERG_WAREHOUSE, upload/profile/pipeline buckets)."
  value       = { for k, b in google_storage_bucket.this : k => b.name }
}

########################################
# Secret Manager
########################################

output "secret_id" {
  description = "Secret Manager secret id backing windrose-secrets (reference from the ExternalSecret remoteRef)."
  value       = google_secret_manager_secret.windrose.secret_id
}

output "secret_resource_name" {
  description = "Fully-qualified Secret Manager resource name."
  value       = google_secret_manager_secret.windrose.name
}

########################################
# Workload Identity service accounts
########################################

output "external_secrets_gsa_email" {
  description = "Annotate the External Secrets KSA with iam.gke.io/gcp-service-account = this."
  value       = google_service_account.external_secrets.email
}

output "storage_gsa_email" {
  description = "Annotate each GCS-using KSA with iam.gke.io/gcp-service-account = this."
  value       = google_service_account.storage.email
}

output "gke_nodes_gsa_email" {
  description = "GKE node pool service account."
  value       = google_service_account.gke_nodes.email
}

########################################
# Artifact Registry
########################################

output "artifact_registry_repo" {
  description = "Artifact Registry docker repo path (empty if create_registry = false)."
  value       = var.create_registry ? "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker[0].repository_id}" : ""
}

########################################
# Workload Identity Federation hints (for CI setup — see README.md)
########################################

output "wif_notes" {
  description = "Reminders for wiring GitHub Actions keyless auth."
  value = {
    workload_identity_pool = local.wi_pool
    ci_secrets_needed      = ["GCP_WIF_PROVIDER", "GCP_DEPLOY_SA", "GKE_CLUSTER", "GCP_REGION"]
    hint                   = "Create a WIF pool + provider for github.com, grant the deploy GSA roles/container.developer + roles/container.clusterViewer, and allow the repo via attribute.repository."
  }
}
