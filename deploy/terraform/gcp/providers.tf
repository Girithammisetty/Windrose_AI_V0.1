# providers.tf — provider wiring.
#
# Authentication is via Application Default Credentials (ADC) / Workload Identity
# Federation only. No service-account key files are referenced anywhere. Locally
# run `gcloud auth application-default login`; in CI the google-github-actions/auth
# action exports ADC from a WIF token (see .github/workflows/cd-gcp.yml).

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}
