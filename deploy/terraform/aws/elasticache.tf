# ElastiCache (Redis/Valkey) replication group — in-transit + at-rest
# encryption, AUTH token, private subnets, reachable only from EKS nodes.
# The platform connects with a rediss:// URL (see secrets.tf: REDIS_URL).

# AUTH token is generated (no special chars — ElastiCache AUTH restriction).
resource "random_password" "redis_auth" {
  length  = 48
  special = false
}

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name_prefix}-redis"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "redis" {
  name        = "${var.name_prefix}-redis"
  description = "Allow Redis from EKS nodes only"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_security_group_rule" "redis_ingress_from_nodes" {
  type                     = "ingress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  security_group_id        = aws_security_group.redis.id
  source_security_group_id = module.eks.node_security_group_id
  description              = "Redis from EKS worker nodes"
}

resource "aws_security_group_rule" "redis_egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.redis.id
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Allow all egress"
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${var.name_prefix}-redis"
  description          = "Windrose cache/dedup/rate-limit"

  engine         = var.redis_engine
  engine_version = var.redis_engine_version
  node_type      = var.redis_node_type
  port           = 6379

  # Single shard, primary + replica(s) with automatic failover.
  num_node_groups            = 1
  replicas_per_node_group    = var.redis_replicas_per_node_group
  automatic_failover_enabled = var.redis_replicas_per_node_group >= 1
  multi_az_enabled           = var.redis_replicas_per_node_group >= 1

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.redis_auth.result

  apply_immediately        = false
  snapshot_retention_limit = 3
}
