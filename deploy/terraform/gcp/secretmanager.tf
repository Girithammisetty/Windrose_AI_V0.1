# secretmanager.tf — the single Secret Manager secret that External Secrets
# syncs into the cluster Secret `windrose-secrets`.
#
# The payload is a JSON object. Infra endpoints are derived from the resources
# created here; everything else comes from var.secrets (filled in LATER). Keys in
# var.secrets OVERRIDE derived keys, so an operator can pin any value explicitly.
#
# The ExternalSecret in values-gcp.yaml should use dataFrom.extract against this
# secret so each JSON key becomes a key in the windrose-secrets K8s Secret.

locals {
  # Endpoints derived from the managed infra.
  derived_secrets = {
    # Postgres (admin/DDL identity; per-service app roles come from var.secrets).
    POSTGRES_HOST           = google_sql_database_instance.pg.private_ip_address
    POSTGRES_PORT           = "5432"
    POSTGRES_ADMIN_USER     = var.postgres_admin_user
    POSTGRES_ADMIN_PASSWORD = local.pg_admin_password

    # Redis — rediss:// (TLS) with AUTH string. Memorystore's default user is "default".
    REDIS_URL = format(
      "rediss://default:%s@%s:%d",
      google_redis_instance.cache.auth_string,
      google_redis_instance.cache.host,
      google_redis_instance.cache.port,
    )

    # Kafka bootstrap (Managed Kafka well-known address, or "" for Pub/Sub path).
    KAFKA_BOOTSTRAP = local.kafka_bootstrap

    # Object storage (GCS via S3-interop endpoint). ACCESS/SECRET keys are left to
    # var.secrets: prefer Workload Identity (no HMAC keys). Provide HMAC keys only
    # if a service needs the S3 protocol path.
    OBJECTSTORE_ENDPOINT = "https://storage.googleapis.com"
    OBJECTSTORE_REGION   = var.region
  }

  # Final payload: derived values first, operator-supplied values win on conflict.
  secret_payload = merge(local.derived_secrets, var.secrets)
}

resource "google_secret_manager_secret" "windrose" {
  secret_id = "${var.name_prefix}-windrose-secrets"

  labels = local.common_labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "windrose" {
  secret      = google_secret_manager_secret.windrose.id
  secret_data = jsonencode(local.secret_payload)
}
