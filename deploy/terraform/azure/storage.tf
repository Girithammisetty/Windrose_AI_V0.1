resource "azurerm_storage_account" "this" {
  name                = substr("${local.compact_name}sa", 0, 24)
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name

  account_tier             = var.storage_account_tier
  account_replication_type = var.storage_replication_type
  account_kind             = "StorageV2"

  # Security posture.
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = true # kept on for S3-compat/static fallback; prefer Workload Identity

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 14
    }
    container_delete_retention_policy {
      days = 14
    }
  }

  # Restrict data-plane to the AKS subnet (service endpoint) while allowing the
  # trusted-Azure-services bypass so Terraform/portal can manage it.
  network_rules {
    default_action             = "Deny"
    bypass                     = ["AzureServices"]
    virtual_network_subnet_ids = [azurerm_subnet.aks.id]
  }

  tags = local.common_tags
}

# Platform blob containers: warehouse (Iceberg), uploads, profiles, pipelines.
resource "azurerm_storage_container" "containers" {
  for_each = toset(var.storage_containers)

  name                  = each.value
  storage_account_name  = azurerm_storage_account.this.name
  container_access_type = "private"
}
