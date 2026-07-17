resource "azurerm_resource_group" "this" {
  name     = "${local.base_name}-rg"
  location = var.location
  tags     = local.common_tags
}
