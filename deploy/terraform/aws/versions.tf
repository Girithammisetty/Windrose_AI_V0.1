# Terraform + provider version pins.
# Keep provider majors pinned so `terraform init` is reproducible across CI runs.
terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Remote state is intentionally NOT configured here so the module can be
  # `init -backend=false` validated. Configure an S3/DynamoDB backend in a
  # `backend.tf` (or via `-backend-config`) before running a real apply.
  # Example:
  #   terraform {
  #     backend "s3" {
  #       bucket         = "windrose-tfstate"
  #       key            = "aws/terraform.tfstate"
  #       region         = "us-east-1"
  #       dynamodb_table = "windrose-tflock"
  #       encrypt        = true
  #     }
  #   }
}
