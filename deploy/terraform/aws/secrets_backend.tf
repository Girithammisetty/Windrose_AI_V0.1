# Optional APP-level secrets path (BYO Infra Hardening Phase 2,
# docs/design/byo-infra-hardening.md): when a deployment sets
# SECRETS_BACKEND=aws (ingestion-service's connector-credential store /
# identity-service's JWT signer both select the AWS adapters), the workload
# needs permission to (a) create/read/delete Secrets Manager entries under its
# own app-secrets namespace and (b) manage an asymmetric KMS signing key —
# distinct from `secrets.tf`'s single `windrose-secrets` blob, which only
# holds INFRA credentials synced by External Secrets Operator.
#
# Off by default (`enable_app_secrets_backend = false`) and a no-op unless
# `secrets_backend = "aws"` — this file adds nothing to a Vault-backed (the
# default) or non-AWS deployment. Mirrors how `secrets.tf` already
# parameterizes infra creds; this does the same for app secrets/signing.

variable "secrets_backend" {
  description = "Which SECRETS_BACKEND the workloads are configured with (vault|aws|azure|gcp). Only used to gate the optional resources in this file — does not itself configure any service."
  type        = string
  default     = "vault"

  validation {
    condition     = contains(["vault", "aws", "azure", "gcp"], var.secrets_backend)
    error_message = "secrets_backend must be one of: vault, aws, azure, gcp."
  }
}

variable "enable_app_secrets_backend" {
  description = "Provision the AWS Secrets Manager + KMS IAM grants for SECRETS_BACKEND=aws (stretch goal of BYO Infra Hardening Phase 2). Off by default; a no-op unless secrets_backend = \"aws\"."
  type        = bool
  default     = false
}

locals {
  app_secrets_backend_enabled = var.enable_app_secrets_backend && var.secrets_backend == "aws"
  app_secrets_path_prefix     = "${var.name_prefix}/app-secrets"
}

# ---------------------------------------------------------------------------
# Secrets Manager: connector credentials (AWSSecretsManagerStore) and the
# folded embed-secret hash, all namespaced under app_secrets_path_prefix so
# this policy can't touch the windrose-secrets infra-creds blob.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "workload_app_secrets" {
  count = local.app_secrets_backend_enabled ? 1 : 0

  statement {
    sid    = "AppSecretsCRUD"
    effect = "Allow"
    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:PutSecretValue",
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
      "secretsmanager:DeleteSecret",
      "secretsmanager:TagResource",
    ]
    resources = [
      "arn:aws:secretsmanager:${var.region}:*:secret:${local.app_secrets_path_prefix}/*",
    ]
  }
}

resource "aws_iam_role_policy" "workload_app_secrets" {
  count  = local.app_secrets_backend_enabled ? 1 : 0
  name   = "app-secrets-manager-access"
  role   = aws_iam_role.workload_s3.id
  policy = data.aws_iam_policy_document.workload_app_secrets[0].json
}

# ---------------------------------------------------------------------------
# KMS: identity-service's AWSKMSSigner creates its own asymmetric signing
# keys at runtime (key rotation, IDN-FR-052) — CreateKey isn't ARN-scopable
# before the key exists, so it's granted account-wide but tag-gated; all
# other operations are scoped to keys carrying that tag.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "workload_app_signing_kms" {
  count = local.app_secrets_backend_enabled ? 1 : 0

  statement {
    sid       = "CreateSigningKeys"
    effect    = "Allow"
    actions   = ["kms:CreateKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/windrose:role"
      values   = ["identity-signer"]
    }
  }

  statement {
    sid    = "TagOnCreate"
    effect = "Allow"
    actions = [
      "kms:TagResource",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "UseSigningKeys"
    effect = "Allow"
    actions = [
      "kms:GetPublicKey",
      "kms:Sign",
      "kms:DescribeKey",
      "kms:ScheduleKeyDeletion",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/windrose:role"
      values   = ["identity-signer"]
    }
  }
}

resource "aws_iam_role_policy" "workload_app_signing_kms" {
  count  = local.app_secrets_backend_enabled ? 1 : 0
  name   = "app-signing-kms-access"
  role   = aws_iam_role.workload_s3.id
  policy = data.aws_iam_policy_document.workload_app_signing_kms[0].json
}
