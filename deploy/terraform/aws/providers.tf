# Provider configuration. Region comes from var.region.
# Credentials are supplied by the environment (CI OIDC role, `aws sso login`,
# or `AWS_PROFILE`) — NEVER hardcoded here.
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "windrose"
      Environment = var.environment
      ManagedBy   = "terraform"
      Component   = "platform-infra"
    }
  }
}

provider "random" {}

provider "tls" {}
