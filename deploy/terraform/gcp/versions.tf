# versions.tf — Terraform + provider version pins.
#
# Pinned deliberately: the managed-Kafka resource (google_managed_kafka_cluster)
# and the GKE Workload-Identity attributes we depend on are only stable in
# google/google-beta >= 5.20. Keep the major pinned to 5.x so `terraform init`
# is reproducible across the team and CI.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.30"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.30"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state is intentionally left to the operator. Uncomment and fill in
  # LATER (or pass `-backend-config` in CI). Do not commit bucket names here.
  #
  # backend "gcs" {
  #   bucket = "REPLACE_ME-windrose-tfstate"
  #   prefix = "windrose/gcp"
  # }
}
