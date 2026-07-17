# Outputs consumed by the CD workflow (cd-aws.yml) and the Helm values-aws.yaml
# wiring. Endpoint values here mirror what is written into Secrets Manager.

output "region" {
  description = "AWS region."
  value       = var.region
}

output "cluster_name" {
  description = "EKS cluster name (feeds `aws eks update-kubeconfig --name`)."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = module.eks.cluster_endpoint
}

output "cluster_oidc_provider_arn" {
  description = "IRSA OIDC provider ARN."
  value       = module.eks.oidc_provider_arn
}

output "cluster_oidc_provider" {
  description = "IRSA OIDC provider URL (without https://)."
  value       = module.eks.oidc_provider
}

output "rds_endpoint" {
  description = "RDS PostgreSQL address (POSTGRES_HOST)."
  value       = aws_db_instance.this.address
}

output "rds_port" {
  description = "RDS PostgreSQL port."
  value       = aws_db_instance.this.port
}

output "redis_primary_endpoint" {
  description = "ElastiCache primary endpoint (host portion of REDIS_URL)."
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "kafka_bootstrap_brokers" {
  description = "MSK SASL/SCRAM bootstrap brokers (KAFKA_BOOTSTRAP)."
  value       = aws_msk_cluster.this.bootstrap_brokers_sasl_scram
}

output "bucket_names" {
  description = "Map of logical bucket name -> actual S3 bucket name."
  value       = { for k, b in aws_s3_bucket.this : k => b.bucket }
}

output "windrose_secret_arn" {
  description = "Secrets Manager ARN External Secrets Operator reads (secrets.remoteRef in values-aws.yaml)."
  value       = aws_secretsmanager_secret.windrose.arn
}

output "windrose_secret_name" {
  description = "Secrets Manager secret name/path."
  value       = aws_secretsmanager_secret.windrose.name
}

output "external_secrets_role_arn" {
  description = "IRSA role ARN for the External Secrets Operator ServiceAccount annotation."
  value       = aws_iam_role.external_secrets.arn
}

output "workload_s3_role_arn" {
  description = "IRSA role ARN for S3-using services' ServiceAccount annotation."
  value       = aws_iam_role.workload_s3.arn
}

output "ecr_repository_urls" {
  description = "ECR repository URLs (empty when create_ecr = false)."
  value       = { for k, r in aws_ecr_repository.service : k => r.repository_url }
}
