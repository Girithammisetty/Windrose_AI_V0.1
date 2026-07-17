########################################
# Core placement / naming
########################################

variable "subscription_id" {
  description = "Azure subscription ID to deploy into."
  type        = string
}

variable "location" {
  description = "Azure region for all resources."
  type        = string
  default     = "eastus"
}

variable "name_prefix" {
  description = "Short lowercase prefix for resource names (letters/numbers only for globally-unique names like storage/ACR)."
  type        = string
  default     = "windrose"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,16}$", var.name_prefix))
    error_message = "name_prefix must be 3-17 chars, start with a letter, lowercase letters/numbers only."
  }
}

variable "environment" {
  description = "Deployment environment (prod, staging, dev). Used in names and tags."
  type        = string
  default     = "prod"
}

variable "tags" {
  description = "Extra tags merged onto every resource."
  type        = map(string)
  default     = {}
}

########################################
# Networking
########################################

variable "vnet_address_space" {
  description = "Address space for the platform VNet."
  type        = list(string)
  default     = ["10.40.0.0/16"]
}

variable "aks_subnet_cidr" {
  description = "Subnet CIDR for AKS nodes/pods (azure CNI)."
  type        = string
  default     = "10.40.0.0/20"
}

variable "postgres_subnet_cidr" {
  description = "Delegated subnet CIDR for PostgreSQL Flexible Server (private access)."
  type        = string
  default     = "10.40.16.0/24"
}

variable "privatelink_subnet_cidr" {
  description = "Subnet CIDR for private endpoints (Redis, etc.)."
  type        = string
  default     = "10.40.17.0/24"
}

########################################
# AKS
########################################

variable "aks_kubernetes_version" {
  description = "Kubernetes version for AKS. null = AKS default for the region."
  type        = string
  default     = null
}

variable "aks_node_vm_size" {
  description = "VM size for the default (system) node pool."
  type        = string
  default     = "Standard_D4s_v5"
}

variable "aks_node_count" {
  description = "Initial node count for the default pool."
  type        = number
  default     = 3
}

variable "aks_node_min_count" {
  description = "Cluster-autoscaler minimum nodes."
  type        = number
  default     = 2
}

variable "aks_node_max_count" {
  description = "Cluster-autoscaler maximum nodes."
  type        = number
  default     = 6
}

variable "aks_service_cidr" {
  description = "Kubernetes service CIDR (must NOT overlap the VNet)."
  type        = string
  default     = "10.41.0.0/16"
}

variable "aks_dns_service_ip" {
  description = "Cluster DNS service IP (must be within aks_service_cidr)."
  type        = string
  default     = "10.41.0.10"
}

########################################
# PostgreSQL Flexible Server
########################################

variable "postgres_sku_name" {
  description = "SKU for PostgreSQL Flexible Server (tier_family_cores), e.g. GP_Standard_D4s_v3."
  type        = string
  default     = "GP_Standard_D4s_v3"
}

variable "postgres_version" {
  description = "PostgreSQL major version."
  type        = string
  default     = "16"
}

variable "postgres_storage_mb" {
  description = "Storage for PostgreSQL Flexible Server in MB."
  type        = number
  default     = 131072 # 128 GiB
}

variable "postgres_admin_username" {
  description = "Administrator (DDL) login for PostgreSQL. The password is taken from the `secrets` map key POSTGRES_ADMIN_PASSWORD, or generated if empty."
  type        = string
  default     = "wradmin"
}

variable "postgres_databases" {
  description = "Per-service databases to create on the shared Flexible Server (the `db:` names in deploy/services.yaml)."
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
# Redis
########################################

variable "redis_sku_name" {
  description = "Azure Cache for Redis SKU: Basic, Standard, or Premium."
  type        = string
  default     = "Standard"
}

variable "redis_capacity" {
  description = "Redis capacity. For Basic/Standard (C family) 0-6; for Premium (P family) 1-5."
  type        = number
  default     = 1
}

########################################
# Event Hubs (Kafka-compatible)
########################################

variable "eventhubs_sku" {
  description = "Event Hubs namespace SKU. Standard or Premium exposes the Kafka endpoint (Basic does not)."
  type        = string
  default     = "Standard"

  validation {
    condition     = contains(["Standard", "Premium"], var.eventhubs_sku)
    error_message = "eventhubs_sku must be Standard or Premium (Basic has no Kafka endpoint)."
  }
}

variable "eventhubs_capacity" {
  description = "Throughput Units (Standard) / Processing Units (Premium) for the namespace."
  type        = number
  default     = 1
}

variable "eventhubs_partition_count" {
  description = "Partition count for each event hub / Kafka topic."
  type        = number
  default     = 4
}

variable "eventhubs_message_retention" {
  description = "Message retention in days for each event hub."
  type        = number
  default     = 7
}

variable "eventhubs_topics" {
  description = "Platform Kafka topics (each becomes one Event Hub). Defaults to the *.events.v1 topics used by the services."
  type        = list(string)
  default = [
    "agent.events.v1",
    "ai.events.v1",
    "audit.events.v1",
    "case.events.v1",
    "chart.events.v1",
    "dataset.events.v1",
    "eval.events.v1",
    "experiment.events.v1",
    "identity.events.v1",
    "inference.events.v1",
    "ingestion.events.v1",
    "memory.events.v1",
    "notification.events.v1",
    "pipeline.events.v1",
    "query.events.v1",
    "rbac.events.v1",
    "security.events.v1",
    "semantic.events.v1",
    "tool.events.v1",
    "usage.events.v1",
  ]
}

########################################
# Storage (Blob object store)
########################################

variable "storage_account_tier" {
  description = "Storage account tier."
  type        = string
  default     = "Standard"
}

variable "storage_replication_type" {
  description = "Storage replication: LRS, ZRS, GRS, RAGRS, GZRS."
  type        = string
  default     = "ZRS"
}

variable "storage_containers" {
  description = "Blob containers to create for the platform."
  type        = list(string)
  default     = ["warehouse", "uploads", "profiles", "pipelines"]
}

########################################
# Container registry (optional)
########################################

variable "create_acr" {
  description = "Create an Azure Container Registry and grant AKS AcrPull. Leave false if you push images to GHCR/ECR/etc."
  type        = bool
  default     = false
}

variable "acr_sku" {
  description = "ACR SKU: Basic, Standard, Premium."
  type        = string
  default     = "Standard"
}

########################################
# Workload Identity / service accounts
########################################

variable "external_secrets_namespace" {
  description = "Namespace where External Secrets Operator runs."
  type        = string
  default     = "external-secrets"
}

variable "external_secrets_service_account" {
  description = "ServiceAccount used by External Secrets Operator (federated to the ESO managed identity)."
  type        = string
  default     = "external-secrets"
}

variable "workload_namespace" {
  description = "Namespace the Windrose services run in."
  type        = string
  default     = "windrose"
}

variable "blob_service_accounts" {
  description = "Service ServiceAccounts that need Blob access via Workload Identity (federated to the blob managed identity)."
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
# Secrets (filled in LATER)
########################################

variable "secrets" {
  description = <<-EOT
    Application secrets written to Key Vault for External Secrets to sync into the
    `windrose-secrets` cluster Secret. Keys use the UPPER_SNAKE names from
    deploy/CONFIG.md (e.g. JWT_SIGNING_KEY_PEM, OPENAI_API_KEY, SMTP_PASSWORD).
    Endpoint-derived secrets (POSTGRES_HOST, REDIS_URL, KAFKA_BOOTSTRAP,
    OBJECTSTORE_*) are computed by Terraform and do NOT need to be provided here;
    anything you do provide overrides the computed value.

    Leave this empty on the first apply and fill values in later via a tfvars file,
    TF_VAR_secrets, or by writing directly to Key Vault. Nothing is hardcoded.
  EOT
  type        = map(string)
  default     = {}
  sensitive   = true
}
