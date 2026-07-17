# gke.tf — regional GKE cluster with Workload Identity, plus one autoscaling
# node pool. The cluster is created with its default node pool removed so the
# managed node pool below is the single source of truth.

# Dedicated GSA for the nodes (least privilege; not the default compute SA).
resource "google_service_account" "gke_nodes" {
  account_id   = "${var.name_prefix}-gke-nodes"
  display_name = "Windrose GKE node pool service account"
}

# Minimal roles for nodes to log, emit metrics, and pull images.
resource "google_project_iam_member" "gke_nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/artifactregistry.reader",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.gke_nodes.email}"
}

resource "google_container_cluster" "this" {
  name     = "${var.name_prefix}-gke"
  location = var.region

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.subnet.id

  # Remove the default pool; use the managed pool below.
  remove_default_node_pool = true
  initial_node_count       = 1

  release_channel {
    channel = var.gke_release_channel
  }

  # Workload Identity — required for the External Secrets + GCS bindings.
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = local.pods_range_name
    services_secondary_range_name = local.services_range_name
  }

  private_cluster_config {
    enable_private_nodes    = var.gke_private_nodes
    enable_private_endpoint = false
    master_ipv4_cidr_block  = var.master_ipv4_cidr
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.master_authorized_networks
      content {
        cidr_block   = cidr_blocks.value.cidr_block
        display_name = cidr_blocks.value.display_name
      }
    }
  }

  # Managed add-ons.
  addons_config {
    http_load_balancing {
      disabled = false
    }
    horizontal_pod_autoscaling {
      disabled = false
    }
    gcp_filestore_csi_driver_config {
      enabled = false
    }
  }

  # Harden: shielded nodes + intranode visibility.
  enable_shielded_nodes = true

  datapath_provider = "ADVANCED_DATAPATH" # Dataplane V2 (eBPF) for NetworkPolicy

  logging_service    = "logging.googleapis.com/kubernetes"
  monitoring_service = "monitoring.googleapis.com/kubernetes"

  deletion_protection = false

  resource_labels = local.common_labels

  # PSA must exist before the cluster relies on private-path connectivity.
  depends_on = [google_service_networking_connection.psa]

  lifecycle {
    ignore_changes = [initial_node_count]
  }
}

resource "google_container_node_pool" "primary" {
  name     = "${var.name_prefix}-primary"
  location = var.region
  cluster  = google_container_cluster.this.name

  autoscaling {
    min_node_count = var.gke_node_min_count
    max_node_count = var.gke_node_max_count
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  upgrade_settings {
    max_surge       = 1
    max_unavailable = 0
  }

  node_config {
    machine_type = var.gke_node_machine_type
    disk_size_gb = var.gke_node_disk_size_gb
    disk_type    = var.gke_node_disk_type
    spot         = var.gke_node_preemptible
    image_type   = "COS_CONTAINERD"

    service_account = google_service_account.gke_nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA" # required for Workload Identity on the nodes
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    labels = local.common_labels

    metadata = {
      disable-legacy-endpoints = "true"
    }
  }

  lifecycle {
    ignore_changes = [node_config[0].labels, node_config[0].taint]
  }
}
