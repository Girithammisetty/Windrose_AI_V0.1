# MSK (managed Kafka) with SASL/SCRAM auth over TLS, in private subnets.
# The platform connects with KAFKA_BOOTSTRAP + KAFKA_SASL_USERNAME/PASSWORD.
#
# SASL/SCRAM on MSK requires the credential to live in a Secrets Manager secret
# whose name is prefixed "AmazonMSK_" and that is encrypted with a customer-
# managed KMS key (the default aws/secretsmanager key is rejected). The same
# username/password is also surfaced in windrose-secrets (see secrets.tf).

resource "random_password" "kafka_scram" {
  length  = 32
  special = false
}

# CMK for the MSK SCRAM secret.
resource "aws_kms_key" "msk_scram" {
  description             = "${var.name_prefix} MSK SASL/SCRAM secret key"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "msk_scram" {
  name          = "alias/${var.name_prefix}-msk-scram"
  target_key_id = aws_kms_key.msk_scram.key_id
}

# Name MUST start with AmazonMSK_ for the SCRAM association to accept it.
resource "aws_secretsmanager_secret" "msk_scram" {
  name       = "AmazonMSK_${var.name_prefix}_scram"
  kms_key_id = aws_kms_key.msk_scram.arn
}

resource "aws_secretsmanager_secret_version" "msk_scram" {
  secret_id = aws_secretsmanager_secret.msk_scram.id
  secret_string = jsonencode({
    username = var.kafka_sasl_username
    password = random_password.kafka_scram.result
  })
}

resource "aws_security_group" "msk" {
  name        = "${var.name_prefix}-msk"
  description = "Allow Kafka from EKS nodes only"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_security_group_rule" "msk_ingress_scram" {
  type                     = "ingress"
  from_port                = 9096
  to_port                  = 9096
  protocol                 = "tcp"
  security_group_id        = aws_security_group.msk.id
  source_security_group_id = module.eks.node_security_group_id
  description              = "Kafka SASL/SCRAM (TLS) from EKS worker nodes"
}

resource "aws_security_group_rule" "msk_ingress_tls" {
  type                     = "ingress"
  from_port                = 9094
  to_port                  = 9094
  protocol                 = "tcp"
  security_group_id        = aws_security_group.msk.id
  source_security_group_id = module.eks.node_security_group_id
  description              = "Kafka TLS from EKS worker nodes"
}

resource "aws_security_group_rule" "msk_egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.msk.id
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Allow all egress"
}

resource "aws_msk_cluster" "this" {
  cluster_name           = "${var.name_prefix}-kafka"
  kafka_version          = var.kafka_version
  number_of_broker_nodes = var.kafka_broker_count

  broker_node_group_info {
    instance_type   = var.kafka_broker_instance_type
    client_subnets  = slice(module.vpc.private_subnets, 0, var.az_count)
    security_groups = [aws_security_group.msk.id]

    storage_info {
      ebs_storage_info {
        volume_size = var.kafka_broker_ebs_size
      }
    }
  }

  client_authentication {
    sasl {
      scram = true
    }
  }

  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  # Broker logs to CloudWatch for observability.
  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.msk.name
      }
    }
  }
}

resource "aws_cloudwatch_log_group" "msk" {
  name              = "/aws/msk/${var.name_prefix}"
  retention_in_days = 14
}

# Associate the SCRAM secret with the cluster so the username/password is valid.
resource "aws_msk_scram_secret_association" "this" {
  cluster_arn     = aws_msk_cluster.this.arn
  secret_arn_list = [aws_secretsmanager_secret.msk_scram.arn]

  depends_on = [aws_secretsmanager_secret_version.msk_scram]
}
