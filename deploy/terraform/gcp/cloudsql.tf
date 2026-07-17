# cloudsql.tf — one Cloud SQL for PostgreSQL instance on a private IP (via PSA).
#
# Per-service databases are created here (one google_sql_database each). The
# per-service *roles* and their passwords are NOT created here: migration Jobs
# (Helm pre-install/upgrade) connect as POSTGRES_ADMIN_USER and create one
# NOBYPASSRLS role per DB, using the POSTGRES_APP_PASSWORD_<DB> secret. This
# keeps RLS role management in the same place as the schema. See README.md.

# Admin password: use the operator-provided value if present, else generate one.
resource "random_password" "pg_admin" {
  length           = 32
  special          = true
  override_special = "_-"
}

locals {
  pg_admin_password = try(
    var.secrets["POSTGRES_ADMIN_PASSWORD"],
    random_password.pg_admin.result,
  )
}

resource "google_sql_database_instance" "pg" {
  name             = "${var.name_prefix}-pg"
  region           = var.region
  database_version = var.cloudsql_version

  deletion_protection = var.cloudsql_deletion_protection

  depends_on = [google_service_networking_connection.psa]

  settings {
    tier              = var.cloudsql_tier
    availability_type = var.cloudsql_availability_type
    disk_size         = var.cloudsql_disk_size_gb
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    user_labels = local.common_labels

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.vpc.id
      enable_private_path_for_google_cloud_services = true
      ssl_mode                                      = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "03:00"
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 14
      }
    }

    maintenance_window {
      day          = 7 # Sunday
      hour         = 4
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled = true
      query_string_length    = 1024
    }

    database_flags {
      name  = "max_connections"
      value = "400"
    }
  }
}

# Admin/DDL role. Migrations use this identity to create per-service roles + schema.
resource "google_sql_user" "admin" {
  name     = var.postgres_admin_user
  instance = google_sql_database_instance.pg.name
  password = local.pg_admin_password
}

# One database per service (from var.databases).
resource "google_sql_database" "db" {
  for_each = toset(var.databases)
  name     = each.value
  instance = google_sql_database_instance.pg.name

  # Deleting a DB drops all its data; require an explicit intent.
  deletion_policy = "ABANDON"
}
