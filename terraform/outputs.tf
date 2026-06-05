output "bucket_name" {
  description = "Name of the S3 landing bucket (set as S3_BUCKET; drop S3_ENDPOINT to use real S3)."
  value       = aws_s3_bucket.landing.id
}

output "bucket_arn" {
  value = aws_s3_bucket.landing.arn
}

output "access_policy_arn" {
  description = "Attach this least-privilege policy to the agent/pipeline identity."
  value       = aws_iam_policy.agent.arn
}

output "irsa_role_arn" {
  description = "IRSA role ARN to annotate on the k8s ServiceAccounts (empty unless OIDC vars set)."
  value       = local.enable_irsa ? aws_iam_role.agent[0].arn : ""
}
