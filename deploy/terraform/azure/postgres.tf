# Generated admin password used only when var.secrets["POSTGRES_ADMIN_PASSWORD"]
# is empty. The effective value is chosen in locals.tf and stored in Key Vault.
resource "random_password" "postgres_admin" {
  length           = 28
  special          = true
  override_special = "!#$%*-_=+"
  min_lower        = 2
  min_upper        = 2
  min_numeric      = 2
  min_special      = 2
}

# Private DNS zone required for Flexible Server private (VNet-integrated) access.
resource "azurerm_private_dns_zone" "postgres" {
  name                = "${var.name_prefix}.private.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "${local.base_name}-pg-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.this.id
  registration_enabled  = false
  tags                  = local.common_tags
}

resource "azurerm_postgresql_flexible_server" "this" {
  name                = "${local.base_name}-pg"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name

  version           = var.postgres_version
  sku_name          = var.postgres_sku_name
  storage_mb        = var.postgres_storage_mb
  auto_grow_enabled = true
  zone              = "1"
  # Private access is implied by delegated_subnet_id + private_dns_zone_id below;
  # public_network_access is computed to Disabled and must not be set explicitly.

  administrator_login    = var.postgres_admin_username
  administrator_password = local.postgres_admin_password

  # Private access: server joins the delegated subnet; DNS resolves inside the VNet.
  delegated_subnet_id = azurerm_subnet.postgres.id
  private_dns_zone_id = azurerm_private_dns_zone.postgres.id

  backup_retention_days        = 14
  geo_redundant_backup_enabled = false

  # The DNS link must exist before the server so the FQDN resolves.
  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]

  tags = local.common_tags

  lifecycle {
    ignore_changes = [zone]
  }
}

# Enforce TLS for all connections.
resource "azurerm_postgresql_flexible_server_configuration" "require_ssl" {
  name      = "require_secure_transport"
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = "on"
}

# Extensions commonly needed by the platform (pgvector for memory-service, etc.).
resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = "VECTOR,UUID-OSSP,PGCRYPTO,CITEXT"
}

# One database per service (the `db:` names in deploy/services.yaml). Migration
# Jobs create schema + the per-DB NOBYPASSRLS runtime roles at deploy time.
resource "azurerm_postgresql_flexible_server_database" "service_dbs" {
  for_each  = toset(var.postgres_databases)
  name      = each.value
  server_id = azurerm_postgresql_flexible_server.this.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}
