# Object storage buckets. Names must be globally unique, so they are suffixed
# with the account id. Consumers reference them via OBJECTSTORE_* / bucket
# outputs, never by hardcoded name.
#
#   warehouse  -> Iceberg table data (query/dataset/pipeline)
#   uploads    -> raw ingestion uploads
#   profiles   -> dataset profiling artifacts
#   pipelines  -> pipeline/MLflow model + run artifacts

data "aws_caller_identity" "current" {}

locals {
  bucket_suffix = data.aws_caller_identity.current.account_id
  buckets = {
    warehouse = "${var.name_prefix}-warehouse-${local.bucket_suffix}"
    uploads   = "${var.name_prefix}-uploads-${local.bucket_suffix}"
    profiles  = "${var.name_prefix}-profiles-${local.bucket_suffix}"
    pipelines = "${var.name_prefix}-pipelines-${local.bucket_suffix}"
  }
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets
  bucket   = each.value

  tags = {
    "windrose.io/bucket" = each.key
  }
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
