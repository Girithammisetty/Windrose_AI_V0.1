# artifactregistry.tf — optional Docker registry for Windrose images.
# Guarded by create_registry so teams already publishing to GHCR/ECR/etc. can
# skip it. When created, its path is us-docker.pkg.dev-style:
#   <region>-docker.pkg.dev/<project>/<repo_id>

resource "google_artifact_registry_repository" "docker" {
  count = var.create_registry ? 1 : 0

  location      = var.region
  repository_id = var.registry_repo_id
  format        = "DOCKER"
  description   = "Windrose service container images"

  labels = local.common_labels

  docker_config {
    immutable_tags = false
  }
}

# Let the GKE nodes pull from this repo (reader). Node GSA already has the
# project-level artifactregistry.reader, this scopes an explicit repo grant too.
resource "google_artifact_registry_repository_iam_member" "node_reader" {
  count = var.create_registry ? 1 : 0

  location   = google_artifact_registry_repository.docker[0].location
  repository = google_artifact_registry_repository.docker[0].repository_id
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.gke_nodes.email}"
}
