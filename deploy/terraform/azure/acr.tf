# Optional Azure Container Registry. Guarded by create_acr so teams pushing to
# GHCR/ECR/etc. can skip it entirely.
resource "azurerm_container_registry" "this" {
  count = var.create_acr ? 1 : 0

  name                = substr("${local.compact_name}acr", 0, 50)
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = var.acr_sku
  admin_enabled       = false

  tags = local.common_tags
}

# Let the AKS kubelet identity pull images from the registry.
resource "azurerm_role_assignment" "aks_acr_pull" {
  count = var.create_acr ? 1 : 0

  scope                = azurerm_container_registry.this[0].id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.this.kubelet_identity[0].object_id
}
