# gcs.tf — object storage buckets: warehouse (Iceberg), uploads (ingestion),
# profiles (dataset/memory), pipelines (pipeline/inference/mlflow artifacts).
# Uniform bucket-level access + versioning on all of them.

locals {
  buckets = {
    warehouse = "${var.name_prefix}-warehouse-${var.project_id}"
    uploads   = "${var.name_prefix}-uploads-${var.project_id}"
    profiles  = "${var.name_prefix}-profiles-${var.project_id}"
    pipelines = "${var.name_prefix}-pipelines-${var.project_id}"
  }
}

resource "google_storage_bucket" "this" {
  for_each = local.buckets

  name     = each.value
  location = var.gcs_location

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.gcs_force_destroy

  versioning {
    enabled = true
  }

  # Reap old noncurrent object versions so versioning does not grow unbounded.
  lifecycle_rule {
    condition {
      num_newer_versions = 5
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 30
    }
    action {
      type = "Delete"
    }
  }

  labels = local.common_labels
}
