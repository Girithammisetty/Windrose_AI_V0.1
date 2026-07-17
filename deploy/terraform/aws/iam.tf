# IRSA roles for in-cluster workloads.
#   1. external-secrets  -> reads the windrose-secrets Secrets Manager secret
#   2. windrose-workload -> least-priv access to the 4 S3 buckets
# Both trust the EKS OIDC provider (module.eks) scoped to a specific
# namespace/ServiceAccount. ARNs are output for the Helm serviceAccount
# annotation `eks.amazonaws.com/role-arn`.

locals {
  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_provider     = module.eks.oidc_provider
}

# ---------------------------------------------------------------------------
# External Secrets Operator role
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "external_secrets_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider}:sub"
      values   = ["system:serviceaccount:${var.external_secrets_namespace}:${var.external_secrets_service_account}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "external_secrets" {
  name               = "${var.name_prefix}-external-secrets-irsa"
  assume_role_policy = data.aws_iam_policy_document.external_secrets_assume.json
}

data "aws_iam_policy_document" "external_secrets" {
  statement {
    sid    = "ReadWindroseSecret"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [
      aws_secretsmanager_secret.windrose.arn,
      "${aws_secretsmanager_secret.windrose.arn}*",
    ]
  }
}

resource "aws_iam_role_policy" "external_secrets" {
  name   = "read-windrose-secrets"
  role   = aws_iam_role.external_secrets.id
  policy = data.aws_iam_policy_document.external_secrets.json
}

# ---------------------------------------------------------------------------
# Workload (S3) role
# ---------------------------------------------------------------------------
locals {
  workload_sa_sub = var.workload_service_account == "*" ? "system:serviceaccount:${var.workload_namespace}:*" : "system:serviceaccount:${var.workload_namespace}:${var.workload_service_account}"
  # StringLike is required when the SA contains a wildcard; harmless otherwise.
}

data "aws_iam_policy_document" "workload_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringLike"
      variable = "${local.oidc_provider}:sub"
      values   = [local.workload_sa_sub]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "workload_s3" {
  name               = "${var.name_prefix}-workload-s3-irsa"
  assume_role_policy = data.aws_iam_policy_document.workload_assume.json
}

data "aws_iam_policy_document" "workload_s3" {
  statement {
    sid       = "ListBuckets"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [for b in aws_s3_bucket.this : b.arn]
  }

  statement {
    sid       = "ObjectRW"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [for b in aws_s3_bucket.this : "${b.arn}/*"]
  }
}

resource "aws_iam_role_policy" "workload_s3" {
  name   = "windrose-bucket-access"
  role   = aws_iam_role.workload_s3.id
  policy = data.aws_iam_policy_document.workload_s3.json
}
