# Real cloud target for the platform: the S3 landing bucket the edge agents upload to and the
# dbt pipeline reads from — the production stand-in for MinIO. Plus least-privilege IAM, and an
# optional IRSA role so EKS pods assume credentials instead of using static keys.

# --------------------------------------------------------------------------- landing bucket
resource "aws_s3_bucket" "landing" {
  bucket = var.bucket_name
  tags   = var.tags
}

resource "aws_s3_bucket_versioning" "landing" {
  bucket = aws_s3_bucket.landing.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "landing" {
  bucket = aws_s3_bucket.landing.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "landing" {
  bucket                  = aws_s3_bucket.landing.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "landing" {
  bucket = aws_s3_bucket.landing.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    filter {} # whole bucket
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "tier-and-expire-captures"
    status = "Enabled"
    filter {} # whole bucket

    transition {
      days          = var.ia_transition_days
      storage_class = "STANDARD_IA"
    }
    expiration {
      days = var.retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = var.retention_days
    }
  }
}

# --------------------------------------------------------------------------- least-priv policy
data "aws_iam_policy_document" "agent" {
  statement {
    sid       = "ListLandingBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.landing.arn]
  }
  statement {
    sid       = "ReadWriteObjects"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.landing.arn}/*"]
  }
}

resource "aws_iam_policy" "agent" {
  name        = "${var.bucket_name}-access"
  description = "Least-privilege access to the edge landing bucket (put/get/list)."
  policy      = data.aws_iam_policy_document.agent.json
  tags        = var.tags
}

# --------------------------------------------------------------------------- optional IRSA role
locals {
  enable_irsa  = var.eks_oidc_provider_arn != "" && var.eks_oidc_provider_url != ""
  oidc_subject = "system:serviceaccount:${var.k8s_namespace}"
}

data "aws_iam_policy_document" "assume_role" {
  count = local.enable_irsa ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.eks_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${replace(var.eks_oidc_provider_url, "https://", "")}:sub"
      values   = [for sa in var.k8s_service_accounts : "${local.oidc_subject}:${sa}"]
    }
    condition {
      test     = "StringEquals"
      variable = "${replace(var.eks_oidc_provider_url, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "agent" {
  count              = local.enable_irsa ? 1 : 0
  name               = "${var.bucket_name}-irsa"
  assume_role_policy = data.aws_iam_policy_document.assume_role[0].json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "agent" {
  count      = local.enable_irsa ? 1 : 0
  role       = aws_iam_role.agent[0].name
  policy_arn = aws_iam_policy.agent.arn
}
