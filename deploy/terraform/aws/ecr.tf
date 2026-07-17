# Optional: one ECR repository per service (from services.yaml / var.service_names).
# Disabled by default (create_ecr = false) so images can be published to an
# external registry such as GHCR instead. Set create_ecr = true to use ECR.

resource "aws_ecr_repository" "service" {
  for_each = var.create_ecr ? toset(var.service_names) : toset([])

  name                 = "${var.name_prefix}/${each.value}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# Expire untagged images after 14 days to control storage cost.
resource "aws_ecr_lifecycle_policy" "service" {
  for_each   = aws_ecr_repository.service
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images older than 14 days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 14
      }
      action = { type = "expire" }
    }]
  })
}
