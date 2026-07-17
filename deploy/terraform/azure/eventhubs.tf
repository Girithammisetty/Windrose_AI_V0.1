resource "azurerm_eventhub_namespace" "this" {
  name                = "${local.base_name}-ehns"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name

  sku      = var.eventhubs_sku
  capacity = var.eventhubs_capacity

  # Kafka protocol endpoint (port 9093). Standard and Premium expose it.
  auto_inflate_enabled     = var.eventhubs_sku == "Standard"
  maximum_throughput_units = var.eventhubs_sku == "Standard" ? var.eventhubs_capacity * 2 : null

  minimum_tls_version = "1.2"

  tags = local.common_tags
}

# One Event Hub per platform Kafka topic. With the Kafka endpoint, the Event Hub
# name IS the Kafka topic name.
resource "azurerm_eventhub" "topics" {
  for_each = toset(var.eventhubs_topics)

  name                = each.value
  namespace_name      = azurerm_eventhub_namespace.this.name
  resource_group_name = azurerm_resource_group.this.name
  partition_count     = var.eventhubs_partition_count
  message_retention   = var.eventhubs_message_retention
}

# Namespace-level SASL credential for the bootstrap producer/consumer.
# Kafka SASL: username = "$ConnectionString", password = this connection string.
resource "azurerm_eventhub_namespace_authorization_rule" "bootstrap" {
  name                = "windrose-bootstrap"
  namespace_name      = azurerm_eventhub_namespace.this.name
  resource_group_name = azurerm_resource_group.this.name

  listen = true
  send   = true
  manage = true
}
