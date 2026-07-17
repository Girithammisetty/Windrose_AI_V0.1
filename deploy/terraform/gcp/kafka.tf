# kafka.tf — event bus.
#
# Primary backend: Managed Service for Apache Kafka (google_managed_kafka_cluster),
# reachable on the private VPC subnet. Managed Kafka authenticates clients with
# GCP IAM over SASL/OAUTHBEARER (via Workload Identity) — there is no static
# SASL username/password, so KAFKA_SASL_USERNAME/PASSWORD stay empty unless you
# front it with your own auth. The bootstrap address is a well-known form:
#   bootstrap.<cluster_id>.<region>.managedkafka.<project>.cloud.goog:9092
#
# Fallback backend: Pub/Sub topics (set kafka_backend = "pubsub"). Use this if
# Managed Kafka is unavailable in your region/provider version. Note: this is a
# DIFFERENT wire protocol — services must use a Pub/Sub adapter, not a Kafka
# client. It exists so the stack still applies; it is not a drop-in Kafka.

########################################
# Managed Kafka (default)
########################################

resource "google_managed_kafka_cluster" "kafka" {
  count = var.kafka_backend == "managed_kafka" ? 1 : 0

  provider   = google-beta
  cluster_id = "${var.name_prefix}-kafka"
  location   = var.region

  capacity_config {
    vcpu_count   = var.kafka_vcpu_count
    memory_bytes = var.kafka_memory_bytes
  }

  gcp_config {
    access_config {
      network_configs {
        subnet = google_compute_subnetwork.subnet.id
      }
    }
  }

  rebalance_config {
    mode = "AUTO_REBALANCE_ON_SCALE_UP"
  }

  labels = local.common_labels
}

########################################
# Pub/Sub fallback (kafka_backend = "pubsub")
########################################

resource "google_pubsub_topic" "events" {
  for_each = var.kafka_backend == "pubsub" ? toset(var.pubsub_topics) : toset([])

  name   = "${var.name_prefix}-${each.value}"
  labels = local.common_labels

  message_retention_duration = "86600s"
}

locals {
  # Well-known Managed Kafka bootstrap address, or empty for the Pub/Sub path.
  kafka_bootstrap = var.kafka_backend == "managed_kafka" ? "bootstrap.${var.name_prefix}-kafka.${var.region}.managedkafka.${var.project_id}.cloud.goog:9092" : ""
}
