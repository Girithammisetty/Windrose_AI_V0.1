# User-assigned managed identity for External Secrets Operator. Reads secrets from
# Key Vault (access policy granted in keyvault.tf) via Workload Identity.
resource "azurerm_user_assigned_identity" "external_secrets" {
  name                = "${local.base_name}-eso-id"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

# User-assigned managed identity for services that read/write Blob storage.
resource "azurerm_user_assigned_identity" "blob" {
  name                = "${local.base_name}-blob-id"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

# Federated credential: ESO ServiceAccount -> ESO identity.
resource "azurerm_federated_identity_credential" "external_secrets" {
  name                = "eso"
  resource_group_name = azurerm_resource_group.this.name
  parent_id           = azurerm_user_assigned_identity.external_secrets.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.this.oidc_issuer_url
  subject             = "system:serviceaccount:${var.external_secrets_namespace}:${var.external_secrets_service_account}"
}

# Federated credentials: each Blob-using service ServiceAccount -> blob identity.
resource "azurerm_federated_identity_credential" "blob" {
  for_each = toset(var.blob_service_accounts)

  name                = "blob-${each.value}"
  resource_group_name = azurerm_resource_group.this.name
  parent_id           = azurerm_user_assigned_identity.blob.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.this.oidc_issuer_url
  subject             = "system:serviceaccount:${var.workload_namespace}:${each.value}"
}

# Data-plane RBAC: blob identity can read/write blobs on the platform account only.
resource "azurerm_role_assignment" "blob_data_contributor" {
  scope                = azurerm_storage_account.this.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.blob.principal_id
}
