# RDS PostgreSQL — single instance in private subnets, reachable only from the
# EKS node security group.
#
# PER-SERVICE DATABASES ARE NOT CREATED HERE.
# services.yaml lists ~19 logical databases (identity, rbac, ingestion, ...).
# Those are created by the platform's migration Jobs (Helm pre-install/upgrade
# hooks) using the admin role provisioned below — this keeps Terraform out of
# the private-subnet data plane (no bastion/`postgresql` provider needed from
# CI) and keeps DB lifecycle owned by the services that own the schema.
# The migration jobs run `CREATE DATABASE ...` + create the per-DB NOBYPASSRLS
# app roles from POSTGRES_APP_PASSWORD_<DB> (supplied via the secrets map).

# Admin password is generated, never hardcoded, and published to Secrets Manager.
resource "random_password" "db_admin" {
  length  = 32
  special = true
  # RDS master password disallows / @ " and spaces.
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.name_prefix}-db"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "rds" {
  name        = "${var.name_prefix}-rds"
  description = "Allow PostgreSQL from EKS nodes only"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_security_group_rule" "rds_ingress_from_nodes" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.rds.id
  source_security_group_id = module.eks.node_security_group_id
  description              = "PostgreSQL from EKS worker nodes"
}

resource "aws_security_group_rule" "rds_egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.rds.id
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Allow all egress"
}

resource "aws_db_parameter_group" "this" {
  name   = "${var.name_prefix}-pg16"
  family = "postgres16"

  # Force TLS for every connection.
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "this" {
  identifier     = "${var.name_prefix}-postgres"
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "postgres"
  username = var.db_admin_username
  password = random_password.db_admin.result
  port     = 5432

  multi_az               = var.db_multi_az
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  parameter_group_name   = aws_db_parameter_group.this.name

  backup_retention_period   = var.db_backup_retention_days
  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = !var.db_deletion_protection
  final_snapshot_identifier = var.db_deletion_protection ? "${var.name_prefix}-postgres-final" : null

  auto_minor_version_upgrade = true
  apply_immediately          = false

  performance_insights_enabled = true
  copy_tags_to_snapshot        = true
}
