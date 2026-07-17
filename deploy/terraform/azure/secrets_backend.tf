# Optional APP-level secrets path (BYO Infra Hardening Phase 2,
# docs/design/byo-infra-hardening.md): when a deployment sets
# SECRETS_BACKEND=azure, ingestion-service's AzureKeyVaultStore and
# identity-service's AzureKeyVaultSigner both need Key Vault secret + key
# permissions. Reuses the SAME Key Vault already provisioned in keyvault.tf
# (one vault, one AZURE_KEY_VAULT_URL) rather than standing up a second one —
# distinct from that file's `azurerm_key_vault_secret.app` entries, which
# only hold INFRA credentials synced by External Secrets Operator; app
# secrets/signing keys are created dynamically by the adapters at runtime, not
# declared here.
#
# Off by default (`enable_app_secrets_backend = false`) and a no-op unless
# `secrets_backend = "azure"` — this file adds nothing to a Vault-backed (the
# default) or non-Azure deployment.

variable "secrets_backend" {
  description = "Which SECRETS_BACKEND the workloads are configured with (vault|aws|azure|gcp). Only used to gate the optional resources in this file — does not itself configure any service."
  type        = string
  default     = "vault"

  validation {
    condition     = contains(["vault", "aws", "azure", "gcp"], var.secrets_backend)
    error_message = "secrets_backend must be one of: vault, aws, azure, gcp."
  }
}

variable "enable_app_secrets_backend" {
  description = "Provision the Key Vault access policy (secrets + keys) for SECRETS_BACKEND=azure (stretch goal of BYO Infra Hardening Phase 2). Off by default; a no-op unless secrets_backend = \"azure\"."
  type        = bool
  default     = false
}

variable "app_secrets_backend_service_accounts" {
  description = "K8s ServiceAccounts (in workload_namespace) that need the app-secrets/signing identity via Workload Identity when the app secrets backend is enabled."
  type        = list(string)
  default     = ["ingestion-service", "identity-service"]
}

locals {
  app_secrets_backend_enabled = var.enable_app_secrets_backend && var.secrets_backend == "azure"
}

resource "azurerm_user_assigned_identity" "app_secrets" {
  count               = local.app_secrets_backend_enabled ? 1 : 0
  name                = "${local.base_name}-app-secrets-id"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

resource "azurerm_federated_identity_credential" "app_secrets" {
  for_each = local.app_secrets_backend_enabled ? toset(var.app_secrets_backend_service_accounts) : toset([])

  name                = "app-secrets-${each.value}"
  resource_group_name = azurerm_resource_group.this.name
  parent_id           = azurerm_user_assigned_identity.app_secrets[0].id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.this.oidc_issuer_url
  subject             = "system:serviceaccount:${var.workload_namespace}:${each.value}"
}

# Secrets: AzureKeyVaultStore.put/get/delete (connector creds + the folded
# embed-secret hash). Keys: AzureKeyVaultSigner.Generate/Sign (asymmetric
# RSA-2048 signing keys, created dynamically — no key declared here).
resource "azurerm_key_vault_access_policy" "app_secrets" {
  count        = local.app_secrets_backend_enabled ? 1 : 0
  key_vault_id = azurerm_key_vault.this.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.app_secrets[0].principal_id

  secret_permissions = ["Get", "Set", "Delete", "List", "Purge"]
  key_permissions    = ["Get", "Create", "Sign", "List"]
}
