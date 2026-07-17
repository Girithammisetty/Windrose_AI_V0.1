# Terraform + provider version pinning.
# azurerm is pinned to the 3.x line intentionally (4.x renamed several arguments
# used below, e.g. enable_auto_scaling -> auto_scaling_enabled).
terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.116"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state is intentionally left unconfigured so `init -backend=false`
  # works out of the box. Wire an azurerm backend in your own backend.tf, e.g.:
  #
  # backend "azurerm" {
  #   resource_group_name  = "tfstate-rg"
  #   storage_account_name = "windrosetfstate"
  #   container_name       = "tfstate"
  #   key                  = "azure/prod.tfstate"
  #   use_oidc             = true
  # }
}
