# versions.tf — provider + Terraform version pins for the Hetzner k3s stack.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.49"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

# The token is credential-shaped — never hardcode. Supply via TF_VAR_hcloud_token
# (a Hetzner Cloud API token, Read & Write) or terraform.tfvars.
provider "hcloud" {
  token = var.hcloud_token
}
