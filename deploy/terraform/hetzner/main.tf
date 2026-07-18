# main.tf — a single k3s control plane + an autoscaling-free agent pool on
# Hetzner Cloud, wired for the Windrose stack.
#
# Design (dev/staging, CPU-only, cheapest):
#   * k3s over a PRIVATE network (flannel on ens10); public NIC only for SSH,
#     ingress (80/443 via built-in Traefik), and image pulls.
#   * Built-in Traefik + klipper servicelb -> NO paid Hetzner load balancer.
#   * hcloud-csi (installed post-apply, see README) -> real block volumes for
#     Postgres / ClickHouse / OpenSearch / MinIO PVCs.
#   * A pre-allocated primary IPv4 on the control plane so its API-server cert
#     carries a stable SAN (kubeconfig works from your laptop after apply).
#   * Shared k3s token generated here and handed to server + agents via
#     user_data, so nodes join declaratively with no post-boot orchestration.

locals {
  common_labels = {
    "managed-by"  = "terraform"
    "project"     = var.name_prefix
    "environment" = var.environment
  }

  cp_private_ip = cidrhost(var.subnet_cidr, 10) # e.g. 10.0.1.10

  # Pick the right k3s install env line: a pinned vX.Y.Z uses INSTALL_K3S_VERSION,
  # otherwise treat the value as a release channel.
  k3s_install_env = can(regex("^v[0-9]", var.k3s_channel)) ? "INSTALL_K3S_VERSION=${var.k3s_channel}" : "INSTALL_K3S_CHANNEL=${var.k3s_channel}"
}

resource "random_password" "k3s_token" {
  length  = 48
  special = false
}

resource "hcloud_ssh_key" "admin" {
  name       = "${var.name_prefix}-admin"
  public_key = var.ssh_public_key
  labels     = local.common_labels
}

# Spread the nodes across distinct physical hosts (availability).
resource "hcloud_placement_group" "this" {
  name   = "${var.name_prefix}-spread"
  type   = "spread"
  labels = local.common_labels
}

########################################
# Private network
########################################

resource "hcloud_network" "this" {
  name     = "${var.name_prefix}-net"
  ip_range = var.network_cidr
  labels   = local.common_labels
}

resource "hcloud_network_subnet" "this" {
  network_id   = hcloud_network.this.id
  type         = "cloud"
  network_zone = var.network_zone
  ip_range     = var.subnet_cidr
}

########################################
# Firewall (applies to the PUBLIC interface only; private net is unfiltered)
########################################

resource "hcloud_firewall" "this" {
  name   = "${var.name_prefix}-fw"
  labels = local.common_labels

  # SSH — lock to admin_cidrs.
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = var.admin_cidrs
  }

  # k3s API server — lock to admin_cidrs.
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "6443"
    source_ips = var.admin_cidrs
  }

  # HTTP / HTTPS ingress (Traefik) — open to the world.
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "80"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "443"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  # ICMP (ping/MTU discovery).
  rule {
    direction  = "in"
    protocol   = "icmp"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
}

########################################
# Control plane
########################################

# Stable public IPv4 for the CP so the API-server cert SAN is known at plan
# time (kubeconfig from your laptop validates TLS).
resource "hcloud_primary_ip" "cp" {
  name        = "${var.name_prefix}-cp-ip"
  type        = "ipv4"
  datacenter  = "${var.location}-dc3" # nbg1 -> nbg1-dc3; adjust if you change location
  auto_delete = false
  labels      = local.common_labels
}

resource "hcloud_server" "control_plane" {
  name               = "${var.name_prefix}-cp-1"
  server_type        = var.control_plane_type
  image              = var.image
  location           = var.location
  ssh_keys           = [hcloud_ssh_key.admin.id]
  placement_group_id = hcloud_placement_group.this.id
  firewall_ids       = [hcloud_firewall.this.id]
  labels             = merge(local.common_labels, { role = "controlplane" })

  public_net {
    ipv4_enabled = true
    ipv4         = hcloud_primary_ip.cp.id
    ipv6_enabled = true
  }

  network {
    network_id = hcloud_network.this.id
    ip         = local.cp_private_ip
  }

  # k3s server: disable servicelb? NO — we keep klipper + Traefik for free
  # ingress. Flannel rides the private NIC (ens10). node-ip is private; the
  # public IP is added as a TLS SAN + external IP for laptop access.
  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    curl -sfL https://get.k3s.io | ${local.k3s_install_env} \
      K3S_TOKEN='${random_password.k3s_token.result}' \
      INSTALL_K3S_EXEC="server \
        --cluster-init \
        --node-name ${var.name_prefix}-cp-1 \
        --node-ip ${local.cp_private_ip} \
        --advertise-address ${local.cp_private_ip} \
        --node-external-ip ${hcloud_primary_ip.cp.ip_address} \
        --tls-san ${hcloud_primary_ip.cp.ip_address} \
        --flannel-iface ens10 \
        --write-kubeconfig-mode 0644" sh -
  EOT

  depends_on = [hcloud_network_subnet.this]
}

########################################
# Agent pool (holds the Windrose workloads)
########################################

resource "hcloud_server" "agent" {
  count = var.agent_count

  name               = "${var.name_prefix}-agent-${count.index + 1}"
  server_type        = var.agent_type
  image              = var.image
  location           = var.location
  ssh_keys           = [hcloud_ssh_key.admin.id]
  placement_group_id = hcloud_placement_group.this.id
  firewall_ids       = [hcloud_firewall.this.id]
  labels             = merge(local.common_labels, { role = "agent" })

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  network {
    network_id = hcloud_network.this.id
    ip         = cidrhost(var.subnet_cidr, 21 + count.index) # 10.0.1.21, .22, ...
  }

  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    curl -sfL https://get.k3s.io | ${local.k3s_install_env} \
      K3S_URL='https://${local.cp_private_ip}:6443' \
      K3S_TOKEN='${random_password.k3s_token.result}' \
      INSTALL_K3S_EXEC="agent \
        --node-name ${var.name_prefix}-agent-${count.index + 1} \
        --node-ip ${cidrhost(var.subnet_cidr, 21 + count.index)} \
        --flannel-iface ens10" sh -
  EOT

  depends_on = [hcloud_server.control_plane]
}
