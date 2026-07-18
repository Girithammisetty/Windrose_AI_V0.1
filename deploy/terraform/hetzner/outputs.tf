# outputs.tf — what you need to reach and operate the cluster.

output "control_plane_ip" {
  description = "Public IPv4 of the k3s control plane (API server + SSH)."
  value       = hcloud_primary_ip.cp.ip_address
}

output "agent_ips" {
  description = "Public IPv4s of the agent nodes (point ingress DNS at any of these)."
  value       = hcloud_server.agent[*].ipv4_address
}

output "network_id" {
  description = "Private network ID (for attaching add-ons / future nodes)."
  value       = hcloud_network.this.id
}

# Fetch a laptop-ready kubeconfig: pull k3s.yaml and rewrite the server URL from
# the in-cluster 127.0.0.1 to the control plane's public IP (already a cert SAN).
output "kubeconfig_command" {
  description = "Run this after apply to write ./kubeconfig for kubectl/helm."
  value = join(" ", [
    "ssh -o StrictHostKeyChecking=accept-new root@${hcloud_primary_ip.cp.ip_address}",
    "'cat /etc/rancher/k3s/k3s.yaml' |",
    "sed 's/127.0.0.1/${hcloud_primary_ip.cp.ip_address}/' > kubeconfig &&",
    "echo 'export KUBECONFIG=$PWD/kubeconfig'",
  ])
}
