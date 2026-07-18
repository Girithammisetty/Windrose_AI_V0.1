# variables.tf — every knob for the Windrose Hetzner (k3s) dev/staging stack.
#
# Tuned for the "dev/staging, CPU-only, cheapest" profile: k3s (not managed
# Kubernetes), built-in Traefik + klipper servicelb (no paid load balancer),
# hcloud-csi for real block volumes, and NO GPU pool (Hetzner Cloud has no GPU
# instances — SLM training stays behind GpuTrainerNotConfigured here).
#
# Credential-shaped vars default to empty and are filled LATER via
# terraform.tfvars / TF_VAR_*. Nothing sensitive is hardcoded.

########################################
# Credentials
########################################

variable "hcloud_token" {
  description = "Hetzner Cloud API token (Read & Write). Prefer TF_VAR_hcloud_token."
  type        = string
  sensitive   = true
}

variable "ssh_public_key" {
  description = "SSH public key authorized on every node (for kubeconfig fetch + debugging)."
  type        = string
}

########################################
# Core naming / placement
########################################

variable "name_prefix" {
  description = "Prefix for all resource names. Keep short, lowercase, RFC1035."
  type        = string
  default     = "windrose"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.name_prefix))
    error_message = "name_prefix must be lowercase alphanumeric/hyphen, start with a letter, 2-20 chars."
  }
}

variable "environment" {
  description = "Environment label (dev|staging). This module targets non-production."
  type        = string
  default     = "staging"
}

variable "location" {
  description = "Hetzner location for the nodes (nbg1|fsn1|hel1 in EU; ash|hil in US)."
  type        = string
  default     = "nbg1"
}

variable "network_zone" {
  description = "Hetzner network zone for the private subnet (must contain `location`). eu-central | us-east | us-west."
  type        = string
  default     = "eu-central"
}

variable "image" {
  description = "Base OS image for all nodes."
  type        = string
  default     = "ubuntu-24.04"
}

########################################
# Networking
########################################

variable "network_cidr" {
  description = "Private network CIDR (nodes talk k3s/flannel over this, never the public NIC)."
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "Private subnet CIDR carved from network_cidr."
  type        = string
  default     = "10.0.1.0/24"
}

variable "admin_cidrs" {
  description = "Source CIDRs allowed to reach SSH (22) and the k3s API (6443). Lock to your IP(s); default is open for first bring-up — TIGHTEN before staging."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}

########################################
# Control plane
########################################

variable "control_plane_type" {
  description = "Server type for the k3s control-plane node. cx22 (2vCPU/4GB) is plenty for a single-CP dev cluster."
  type        = string
  default     = "cx22"
}

########################################
# Agent (worker) pool — holds the whole Windrose stack
########################################

variable "agent_count" {
  description = "Number of k3s agent nodes. 3 gives ~48GB across cpx41-class nodes — comfortable for all ~22 services + infra + CPU Ollama."
  type        = number
  default     = 3
}

variable "agent_type" {
  description = "Server type for agent nodes. cpx41 = 8 vCPU / 16 GB (shared AMD). Bump to ccx-line for dedicated vCPU if noisy-neighbor latency bites."
  type        = string
  default     = "cpx41"
}

########################################
# k3s
########################################

variable "k3s_channel" {
  description = "k3s install channel (stable|latest) or a pinned version like v1.31.4+k3s1. `stable` tracks the current stable minor."
  type        = string
  default     = "stable"
}
