# memorystore.tf — Memorystore for Redis on the private VPC, with AUTH + TLS.
#
# REDIS_URL is assembled in secretmanager.tf as:
#   rediss://default:<auth_string>@<host>:<port>
# (rediss:// => TLS; SERVER_AUTHENTICATION issues a server cert the client validates.)

resource "google_redis_instance" "cache" {
  name           = "${var.name_prefix}-redis"
  region         = var.region
  tier           = var.redis_tier
  memory_size_gb = var.redis_memory_size_gb
  redis_version  = var.redis_version

  # Private Service Access path (same peering as Cloud SQL).
  connect_mode            = "PRIVATE_SERVICE_ACCESS"
  authorized_network      = google_compute_network.vpc.id
  auth_enabled            = true
  transit_encryption_mode = "SERVER_AUTHENTICATION"

  redis_configs = {
    maxmemory-policy = "allkeys-lru"
  }

  labels = local.common_labels

  depends_on = [google_service_networking_connection.psa]
}
