# Windrose on Hetzner Cloud (k3s) — dev / staging

The cheapest way to run the full Windrose stack on a real Kubernetes cluster.
Profile: **dev/staging, CPU-only, cost-first.** A single k3s control plane +
a 3-node agent pool on Hetzner Cloud, with the data tier self-hosted in-cluster.

This is the fourth target next to `../aws`, `../gcp`, `../azure`. It uses **k3s**
(not managed Kubernetes) because Hetzner's managed offering is newer/limited and
k3s is dramatically cheaper for this stage.

## What it is NOT
- **No GPU pool.** Hetzner Cloud has no GPU instances, so SLM training stays
  behind `GpuTrainerNotConfigured` here. Use GCP/AWS (`gpu_training_pool.tf`)
  when you need the trainer live.
- **No managed Postgres / native object store / cloud secret manager.** Those
  are self-hosted in-cluster (see step 4). That's the cost trade for dev.

## Cost shape (approximate Hetzner list price — verify current rates)
| Item | Qty | Notes |
|---|---|---|
| Control plane `cx22` (2 vCPU / 4 GB) | 1 | k3s server |
| Agent `cpx41` (8 vCPU / 16 GB) | 3 | ~48 GB total — holds all ~22 services + infra + CPU Ollama |
| Block volumes (hcloud-csi) | as needed | Postgres/ClickHouse/OpenSearch/MinIO PVCs |
| Load balancer | 0 | Traefik + klipper servicelb → **no paid LB** |

Two ways to stand up the cluster — pick one.

---

## Option A — Terraform (this module, matches the deploy/ convention)

```bash
cd deploy/terraform/hetzner
cp terraform.tfvars.example terraform.tfvars     # set ssh_public_key + admin_cidrs
export TF_VAR_hcloud_token=...                    # Hetzner Cloud API token (R/W)

terraform init
terraform apply

# Write a laptop kubeconfig (the command is printed as an output):
eval "$(terraform output -raw kubeconfig_command)"
kubectl get nodes    # cp-1 + 3 agents => Ready
```

### Post-apply: install the block-storage CSI (one time)
The Terraform keeps the control plane minimal; add real volumes with the hcloud
CSI driver (needs the same API token as a Secret):

```bash
kubectl -n kube-system create secret generic hcloud \
  --from-literal=token="$TF_VAR_hcloud_token"
kubectl apply -f https://raw.githubusercontent.com/hetznercloud/csi-driver/main/deploy/kubernetes/hcloud-csi.yml
kubectl get storageclass    # hcloud-volumes appears
```

## Option B — `hetzner-k3s` CLI (simplest; installs CCM + CSI for you)

```bash
brew install vitobotta/tap/hetzner_k3s
export HCLOUD_TOKEN=...
hetzner-k3s create --config hetzner-k3s.example.yaml   # writes ./kubeconfig
```
This bundles the Cloud Controller Manager + CSI, so `hcloud-volumes` is ready
immediately and you can skip the CSI step above.

---

## Then: deploy Windrose (either option)

1. **Data tier in-cluster.** Deploy the same components you run locally in
   `deploy/docker-compose.dev.yml` — Postgres, Redpanda (or Kafka), MinIO,
   ClickHouse, OpenSearch, Redis, OPA, Ollama — as Deployments/StatefulSets with
   `hcloud-volumes` PVCs. Point Ollama at a small model:
   `kubectl exec deploy/ollama -- ollama pull llama3.2:3b`.

2. **Secrets.** No cloud secret manager here — create `windrose-secrets`
   directly (full key list in `deploy/CONFIG.md`):
   ```bash
   kubectl create secret generic windrose-secrets \
     --from-literal=POSTGRES_HOST=postgres --from-literal=POSTGRES_PORT=5432 \
     --from-literal=KAFKA_BOOTSTRAP=redpanda:9092 \
     --from-literal=REDIS_URL=redis://redis:6379/0 # ...
   ```

3. **App chart.**
   ```bash
   helm upgrade --install windrose deploy/helm/windrose \
     -f deploy/helm/windrose/values-hetzner.yaml \
     --set global.imageTag=<sha>
   ```

4. **Ingress DNS.** Point an A record at any agent node's public IP
   (`terraform output agent_ips`); Traefik serves 80/443.

## Security note
`admin_cidrs` defaults to world-open for first bring-up. **Tighten it to your
IP/32** (SSH + k3s API 6443) before this cluster holds anything real.

## Validate without applying
`terraform init && terraform validate` checks the module with no Hetzner account
or spend — same as the other cloud modules.
