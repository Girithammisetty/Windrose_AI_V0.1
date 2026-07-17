resource "azurerm_kubernetes_cluster" "this" {
  name                = "${local.base_name}-aks"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  dns_prefix          = local.base_name
  kubernetes_version  = var.aks_kubernetes_version

  # Workload Identity: publish the OIDC issuer and enable the webhook so pods can
  # federate to the user-assigned managed identities created in identity.tf.
  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  default_node_pool {
    name                 = "system"
    vm_size              = var.aks_node_vm_size
    vnet_subnet_id       = azurerm_subnet.aks.id
    orchestrator_version = var.aks_kubernetes_version
    os_disk_size_gb      = 128
    max_pods             = 60
    type                 = "VirtualMachineScaleSets"

    enable_auto_scaling = true
    node_count          = var.aks_node_count
    min_count           = var.aks_node_min_count
    max_count           = var.aks_node_max_count

    upgrade_settings {
      max_surge = "33%"
    }
  }

  # Control-plane identity. The kubelet identity is created automatically and used
  # for AcrPull (see acr.tf).
  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin    = "azure"
    network_policy    = "azure"
    load_balancer_sku = "standard"
    service_cidr      = var.aks_service_cidr
    dns_service_ip    = var.aks_dns_service_ip
  }

  oms_agent {
    log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  }

  tags = local.common_tags

  lifecycle {
    ignore_changes = [
      # Autoscaler adjusts node_count at runtime; do not fight it.
      default_node_pool[0].node_count,
    ]
  }
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${local.base_name}-logs"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.common_tags
}
