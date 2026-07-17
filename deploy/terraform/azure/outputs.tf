########################################
# Resource group / AKS
########################################

output "resource_group_name" {
  description = "Resource group holding all platform infra."
  value       = azurerm_resource_group.this.name
}

output "location" {
  description = "Azure region."
  value       = azurerm_resource_group.this.location
}

output "aks_cluster_name" {
  description = "AKS cluster name (use for `az aks get-credentials`)."
  value       = azurerm_kubernetes_cluster.this.name
}

output "aks_oidc_issuer_url" {
  description = "AKS OIDC issuer URL (Workload Identity federation subject issuer)."
  value       = azurerm_kubernetes_cluster.this.oidc_issuer_url
}

########################################
# Data services (endpoints -> also written to Key Vault)
########################################

output "postgres_fqdn" {
  description = "PostgreSQL Flexible Server FQDN (POSTGRES_HOST)."
  value       = azurerm_postgresql_flexible_server.this.fqdn
}

output "postgres_databases" {
  description = "Per-service databases created on the shared server."
  value       = [for db in azurerm_postgresql_flexible_server_database.service_dbs : db.name]
}

output "redis_hostname" {
  description = "Redis hostname (TLS port 6380)."
  value       = azurerm_redis_cache.this.hostname
}

output "eventhubs_namespace" {
  description = "Event Hubs namespace name."
  value       = azurerm_eventhub_namespace.this.name
}

output "kafka_bootstrap" {
  description = "Kafka bootstrap endpoint (Event Hubs, port 9093) = KAFKA_BOOTSTRAP."
  value       = "${azurerm_eventhub_namespace.this.name}.servicebus.windows.net:9093"
}

output "eventhubs_topics" {
  description = "Event Hubs / Kafka topics provisioned."
  value       = [for h in azurerm_eventhub.topics : h.name]
}

########################################
# Storage
########################################

output "storage_account_name" {
  description = "Blob storage account (OBJECTSTORE_ACCOUNT)."
  value       = azurerm_storage_account.this.name
}

output "storage_blob_endpoint" {
  description = "Primary blob endpoint (OBJECTSTORE_ENDPOINT)."
  value       = azurerm_storage_account.this.primary_blob_endpoint
}

output "storage_containers" {
  description = "Blob containers created."
  value       = [for c in azurerm_storage_container.containers : c.name]
}

########################################
# Key Vault
########################################

output "key_vault_name" {
  description = "Key Vault name."
  value       = azurerm_key_vault.this.name
}

output "key_vault_uri" {
  description = "Key Vault URI (ClusterSecretStore vaultUrl)."
  value       = azurerm_key_vault.this.vault_uri
}

output "key_vault_secret_name_map" {
  description = "Map of UPPER_SNAKE env key -> hyphenated Key Vault secret name (for the ExternalSecret remoteRefs)."
  value       = local.key_vault_secret_names
}

########################################
# Workload Identity client IDs (serviceAccount annotations)
########################################

output "external_secrets_identity_client_id" {
  description = "Client ID to annotate the ESO ServiceAccount (azure.workload.identity/client-id)."
  value       = azurerm_user_assigned_identity.external_secrets.client_id
}

output "blob_identity_client_id" {
  description = "Client ID to annotate Blob-using service ServiceAccounts (azure.workload.identity/client-id)."
  value       = azurerm_user_assigned_identity.blob.client_id
}

output "tenant_id" {
  description = "AAD tenant ID (ClusterSecretStore / workload identity)."
  value       = data.azurerm_client_config.current.tenant_id
}

########################################
# ACR (optional)
########################################

output "acr_login_server" {
  description = "ACR login server (null if create_acr = false)."
  value       = var.create_acr ? azurerm_container_registry.this[0].login_server : null
}
