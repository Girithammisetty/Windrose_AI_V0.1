# Provider configuration.
#
# Authentication is NEVER embedded in code. The azurerm provider picks up
# credentials from the environment, in order of preference:
#   * OIDC / Workload Identity Federation (CI):  ARM_USE_OIDC=true,
#     ARM_CLIENT_ID, ARM_TENANT_ID, ARM_SUBSCRIPTION_ID (token injected by the CI)
#   * Azure CLI (local dev):                      `az login`
#   * Service principal env vars:                 ARM_CLIENT_SECRET (discouraged)
#
# subscription_id is passed as a variable so the same code targets any subscription.
provider "azurerm" {
  features {
    key_vault {
      # Keep soft-deleted vaults recoverable; do not let `destroy` purge them.
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
    resource_group {
      prevent_deletion_if_contains_resources = true
    }
  }

  subscription_id = var.subscription_id

  # Honour ARM_USE_OIDC / ARM_USE_CLI from the environment; no secrets here.
  storage_use_azuread = true
}
