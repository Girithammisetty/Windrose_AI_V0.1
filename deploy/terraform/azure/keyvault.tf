# Key Vault names are globally unique; add a short random suffix.
resource "random_string" "kv_suffix" {
  length  = 5
  upper   = false
  special = false
}

resource "azurerm_key_vault" "this" {
  name                = substr("${local.compact_name}kv${random_string.kv_suffix.result}", 0, 24)
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  enabled_for_disk_encryption   = false
  purge_protection_enabled      = true
  soft_delete_retention_days    = 7
  public_network_access_enabled = true # ESO reaches it via AAD; lock down with a private endpoint if required

  # The identity/principal running Terraform: full secret management so `apply`
  # can write the secret values.
  access_policy {
    tenant_id = data.azurerm_client_config.current.tenant_id
    object_id = data.azurerm_client_config.current.object_id

    secret_permissions = [
      "Get", "List", "Set", "Delete", "Purge", "Recover",
    ]
  }

  # External Secrets Operator identity: read-only.
  access_policy {
    tenant_id = data.azurerm_client_config.current.tenant_id
    object_id = azurerm_user_assigned_identity.external_secrets.principal_id

    secret_permissions = [
      "Get", "List",
    ]
  }

  tags = local.common_tags
}

# Application + endpoint secrets. Names are hyphenated (Key Vault forbids "_");
# External Secrets maps them back to UPPER_SNAKE keys in `windrose-secrets`.
resource "azurerm_key_vault_secret" "app" {
  for_each = local.all_secrets

  name         = local.key_vault_secret_names[each.key]
  value        = each.value
  key_vault_id = azurerm_key_vault.this.id
  content_type = "text/plain"

  tags = {
    env_key = each.key
  }
}
