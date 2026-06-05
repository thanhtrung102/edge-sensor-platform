variable "region" {
  description = "AWS region for the landing bucket."
  type        = string
  default     = "ap-southeast-1" # Singapore — closest to Hanoi
}

variable "bucket_name" {
  description = "Globally-unique name for the S3 landing bucket (the cloud target MinIO stands in for)."
  type        = string
  default     = "edge-sensor-platform-landing"
}

variable "retention_days" {
  description = "Expire landed objects after this many days (raw captures are bulky; keep the lake lean)."
  type        = number
  default     = 30
}

variable "ia_transition_days" {
  description = "Transition objects to STANDARD_IA after this many days."
  type        = number
  default     = 7
}

# --- Optional IRSA wiring: set both to mint a role the edge agent / pipeline pods assume on EKS,
#     instead of static access keys. This is the production replacement for the MinIO credentials.
variable "eks_oidc_provider_arn" {
  description = "EKS cluster OIDC provider ARN. Empty = skip the IRSA role."
  type        = string
  default     = ""
}

variable "eks_oidc_provider_url" {
  description = "EKS cluster OIDC provider URL (https://oidc.eks...). Empty = skip the IRSA role."
  type        = string
  default     = ""
}

variable "k8s_namespace" {
  description = "Kubernetes namespace the workloads run in."
  type        = string
  default     = "edge"
}

variable "k8s_service_accounts" {
  description = "Service accounts allowed to assume the role (edge agent + pipeline)."
  type        = list(string)
  default     = ["edge-agent", "edge-pipeline"]
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default = {
    project = "edge-sensor-platform"
    managed = "terraform"
  }
}
