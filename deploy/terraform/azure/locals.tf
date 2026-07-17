data "azurerm_client_config" "current" {}

locals {
  # Common naming + tags.
  base_name = "${var.name_prefix}-${var.environment}"

  common_tags = merge({
    platform    = "windrose"
    environment = var.environment
    managed_by  = "terraform"
  }, var.tags)

  # Globally-unique names must be alphanumeric and lowercase; strip separators.
  compact_name = replace(local.base_name, "-", "")

  # Redis family is derived from the SKU: C for Basic/Standard, P for Premium.
  redis_family = var.redis_sku_name == "Premium" ? "P" : "C"

  # Admin password for PostgreSQL: use the provided secret if non-empty, else the
  # generated one. Never hardcoded.
  postgres_admin_password = coalesce(
    lookup(var.secrets, "POSTGRES_ADMIN_PASSWORD", ""),
    random_password.postgres_admin.result,
  )

  # Endpoints Terraform can compute. These are merged UNDER var.secrets so that a
  # user-provided value always wins.
  computed_secrets = {
    POSTGRES_HOST           = azurerm_postgresql_flexible_server.this.fqdn
    POSTGRES_PORT           = "5432"
    POSTGRES_ADMIN_USER     = var.postgres_admin_username
    POSTGRES_ADMIN_PASSWORD = local.postgres_admin_password

    REDIS_URL = format("rediss://:%s@%s:%d",
      azurerm_redis_cache.this.primary_access_key,
      azurerm_redis_cache.this.hostname,
      azurerm_redis_cache.this.ssl_port,
    )

    KAFKA_BOOTSTRAP     = "${azurerm_eventhub_namespace.this.name}.servicebus.windows.net:9093"
    KAFKA_SASL_USERNAME = "$ConnectionString"
    KAFKA_SASL_PASSWORD = azurerm_eventhub_namespace_authorization_rule.bootstrap.primary_connection_string

    OBJECTSTORE_ENDPOINT   = azurerm_storage_account.this.primary_blob_endpoint
    OBJECTSTORE_REGION     = var.location
    OBJECTSTORE_ACCOUNT    = azurerm_storage_account.this.name
    OBJECTSTORE_ACCESS_KEY = azurerm_storage_account.this.name
    OBJECTSTORE_SECRET_KEY = azurerm_storage_account.this.primary_access_key
  }

  # Final Key Vault secret set. user-provided (var.secrets) overrides computed.
  # NOTE: Key Vault secret names cannot contain "_", so each name is hyphenated
  # (POSTGRES_HOST -> postgres-host). External Secrets maps them back to the
  # UPPER_SNAKE keys expected in the `windrose-secrets` cluster Secret.
  all_secrets = merge(local.computed_secrets, var.secrets)

  key_vault_secret_names = {
    for k, v in local.all_secrets : k => lower(replace(k, "_", "-"))
  }
}
