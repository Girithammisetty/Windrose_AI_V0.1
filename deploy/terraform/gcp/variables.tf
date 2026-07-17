# variables.tf — every knob for the Windrose GCP stack.
#
# Anything credential-shaped defaults to empty and is filled in LATER via
# terraform.tfvars / TF_VAR_* / CI. Nothing sensitive is hardcoded.

########################################
# Core project / naming
########################################

variable "project_id" {
  description = "GCP project ID to deploy into."
  type        = string
}

variable "region" {
  description = "Primary GCP region (regional GKE, Cloud SQL, Memorystore, Kafka)."
  type        = string
  default     = "us-central1"
}

variable "name_prefix" {
  description = "Prefix for all resource names and the Secret Manager secret. Keep short (<= 20 chars), lowercase, RFC1035."
  type        = string
  default     = "windrose"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.name_prefix))
    error_message = "name_prefix must be lowercase alphanumeric/hyphen, start with a letter, 2-20 chars."
  }
}

variable "environment" {
  description = "Environment label (dev|staging|production). Applied as a resource label."
  type        = string
  default     = "production"
}

variable "labels" {
  description = "Extra labels merged onto every resource that supports labels."
  type        = map(string)
  default     = {}
}

########################################
# Networking
########################################

variable "subnet_cidr" {
  description = "Primary subnet CIDR (GKE nodes)."
  type        = string
  default     = "10.10.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary range CIDR for GKE pods."
  type        = string
  default     = "10.20.0.0/16"
}

variable "services_cidr" {
  description = "Secondary range CIDR for GKE services (ClusterIPs)."
  type        = string
  default     = "10.30.0.0/20"
}

variable "master_ipv4_cidr" {
  description = "CIDR /28 for the private GKE control-plane endpoint."
  type        = string
  default     = "172.16.0.0/28"
}

variable "psa_prefix_length" {
  description = "Prefix length for the Private Service Access range reserved for Cloud SQL / Memorystore peering."
  type        = number
  default     = 16
}

variable "master_authorized_networks" {
  description = "CIDRs allowed to reach the GKE public control-plane endpoint. Empty => only in-VPC / via CI get-credentials over the private path where enabled."
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}

########################################
# GKE
########################################

variable "gke_release_channel" {
  description = "GKE release channel (RAPID|REGULAR|STABLE)."
  type        = string
  default     = "REGULAR"
}

variable "gke_node_machine_type" {
  description = "Machine type for the primary node pool."
  type        = string
  default     = "e2-standard-4"
}

variable "gke_node_min_count" {
  description = "Min nodes per zone in the primary node pool (autoscaling)."
  type        = number
  default     = 1
}

variable "gke_node_max_count" {
  description = "Max nodes per zone in the primary node pool (autoscaling)."
  type        = number
  default     = 5
}

variable "gke_node_disk_size_gb" {
  description = "Node boot disk size (GB)."
  type        = number
  default     = 100
}

variable "gke_node_disk_type" {
  description = "Node boot disk type."
  type        = string
  default     = "pd-balanced"
}

variable "gke_node_preemptible" {
  description = "Use spot/preemptible nodes for the primary pool (cheaper, non-prod)."
  type        = bool
  default     = false
}

variable "gke_private_nodes" {
  description = "Give nodes private IPs only (egress via Cloud NAT)."
  type        = bool
  default     = true
}

########################################
# Cloud SQL (PostgreSQL)
########################################

variable "cloudsql_tier" {
  description = "Cloud SQL machine tier (e.g. db-custom-2-8192 or db-g1-small)."
  type        = string
  default     = "db-custom-2-8192"
}

variable "cloudsql_version" {
  description = "Cloud SQL PostgreSQL version enum (e.g. POSTGRES_16)."
  type        = string
  default     = "POSTGRES_16"
}

variable "cloudsql_disk_size_gb" {
  description = "Initial Cloud SQL data disk size (GB). Autoresize is enabled."
  type        = number
  default     = 50
}

variable "cloudsql_availability_type" {
  description = "REGIONAL (HA, prod) or ZONAL (single zone, cheaper)."
  type        = string
  default     = "REGIONAL"
}

variable "cloudsql_deletion_protection" {
  description = "Block accidental instance deletion."
  type        = bool
  default     = true
}

variable "postgres_admin_user" {
  description = "Admin/DDL role name written to POSTGRES_ADMIN_USER. Migrations use this to create per-service roles."
  type        = string
  default     = "windrose_admin"
}

variable "databases" {
  description = "Per-service Postgres databases to create (from deploy/services.yaml `db:` fields, excluding ~)."
  type        = list(string)
  default = [
    "identity",
    "rbac",
    "ingestion",
    "dataset",
    "realtimehub",
    "agent_runtime",
    "memory",
    "case_svc",
    "tool_plane",
    "ai_gateway",
    "pipeline",
    "experiment",
    "inference",
    "query",
    "semantic",
    "chart",
    "usage",
    "notification",
    "eval",
  ]
}

########################################
# Memorystore (Redis)
########################################

variable "redis_tier" {
  description = "Memorystore tier: BASIC (single node) or STANDARD_HA (replicated)."
  type        = string
  default     = "STANDARD_HA"
}

variable "redis_memory_size_gb" {
  description = "Memorystore capacity (GB)."
  type        = number
  default     = 4
}

variable "redis_version" {
  description = "Memorystore Redis version enum (e.g. REDIS_7_2)."
  type        = string
  default     = "REDIS_7_2"
}

########################################
# Kafka
########################################

variable "kafka_backend" {
  description = "Event-bus backend: 'managed_kafka' (google_managed_kafka_cluster) or 'pubsub' (Pub/Sub topics fallback)."
  type        = string
  default     = "managed_kafka"

  validation {
    condition     = contains(["managed_kafka", "pubsub"], var.kafka_backend)
    error_message = "kafka_backend must be either 'managed_kafka' or 'pubsub'."
  }
}

variable "kafka_vcpu_count" {
  description = "Managed Kafka: total vCPUs across the cluster (>= 3)."
  type        = number
  default     = 3
}

variable "kafka_memory_bytes" {
  description = "Managed Kafka: total memory in bytes (>= 1 GiB per vCPU; default 12 GiB)."
  type        = number
  default     = 12884901888
}

variable "pubsub_topics" {
  description = "Topic names created when kafka_backend = 'pubsub'."
  type        = list(string)
  default     = ["windrose-events", "windrose-dlq"]
}

########################################
# GCS buckets
########################################

variable "gcs_location" {
  description = "Location for GCS buckets (region or multi-region, e.g. US)."
  type        = string
  default     = "US"
}

variable "gcs_force_destroy" {
  description = "Allow `terraform destroy` to delete non-empty buckets (non-prod only)."
  type        = bool
  default     = false
}

########################################
# Artifact Registry
########################################

variable "create_registry" {
  description = "Create an Artifact Registry docker repo in this project."
  type        = bool
  default     = true
}

variable "registry_repo_id" {
  description = "Artifact Registry repository ID (docker format)."
  type        = string
  default     = "windrose"
}

########################################
# Kubernetes / Workload Identity wiring
########################################

variable "k8s_namespace" {
  description = "Namespace the Helm chart deploys Windrose services into."
  type        = string
  default     = "windrose"
}

variable "external_secrets_namespace" {
  description = "Namespace the External Secrets Operator runs in."
  type        = string
  default     = "external-secrets"
}

variable "external_secrets_ksa" {
  description = "K8s ServiceAccount name used by External Secrets Operator (bound to the accessor GSA via Workload Identity)."
  type        = string
  default     = "external-secrets"
}

variable "gcs_workload_ksas" {
  description = "K8s ServiceAccount names (in k8s_namespace) that need GCS access, bound to the storage GSA via Workload Identity. These are the SAs the Helm chart annotates."
  type        = list(string)
  default = [
    "ingestion-service",
    "dataset-service",
    "query-service",
    "pipeline-orchestrator",
    "inference-service",
    "mlflow",
  ]
}

########################################
# Secrets payload (filled in LATER)
########################################

# The full set of keys expected in the cluster Secret `windrose-secrets`
# (see deploy/CONFIG.md). Infra-derived keys (POSTGRES_HOST, REDIS_URL,
# KAFKA_BOOTSTRAP, OBJECTSTORE_*, POSTGRES_ADMIN_*) are injected automatically
# by secretmanager.tf and DO NOT need to be provided here. Everything else
# (JWT keys, Keycloak/SMTP/LLM/ClickHouse/Vault creds, per-DB app passwords)
# is supplied here later. Anything you set here overrides the derived value.
variable "secrets" {
  description = "Extra key/value pairs merged into the windrose-secrets payload. Fill in LATER; defaults empty so nothing is committed."
  type        = map(string)
  default     = {}
  sensitive   = true
}
