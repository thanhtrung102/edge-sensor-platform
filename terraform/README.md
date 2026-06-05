# `terraform/` — the real AWS landing target (IaC)

Provisions the cloud half of the platform: the **S3 landing bucket** the edge agents upload to
and the dbt pipeline reads from — the production stand-in for the local MinIO — plus
**least-privilege IAM** and an **optional IRSA role** so EKS pods assume credentials instead of
using static keys.

This is the concrete "swap MinIO for S3" from [`k8s/README.md`](../k8s/README.md): MinIO→S3,
the `object-store` Secret → this IAM/IRSA.

## What it creates
- `aws_s3_bucket` (+ versioning, AES256 encryption, full public-access block)
- `aws_s3_bucket_lifecycle_configuration` — abort stale multipart uploads, tier to `STANDARD_IA`,
  expire bulky captures after `retention_days` (a landing zone shouldn't hoard raw frames)
- `aws_iam_policy` — least-privilege `s3:PutObject/GetObject/DeleteObject` + `ListBucket`
- `aws_iam_role` (IRSA) — **only if** `eks_oidc_provider_arn` / `eks_oidc_provider_url` are set;
  trusts the `edge-agent` / `edge-pipeline` ServiceAccounts in the `edge` namespace

## Use
```bash
cd terraform
terraform init
terraform apply -var bucket_name=my-unique-edge-landing

# wire the platform to real S3: set S3_BUCKET to the output, omit S3_ENDPOINT (defaults to AWS S3),
# and grant the agent the access_policy_arn (or annotate the SA with irsa_role_arn on EKS).
```
Enable IRSA:
```bash
terraform apply \
  -var bucket_name=my-unique-edge-landing \
  -var eks_oidc_provider_arn=arn:aws:iam::<acct>:oidc-provider/oidc.eks.<region>.amazonaws.com/id/<id> \
  -var eks_oidc_provider_url=https://oidc.eks.<region>.amazonaws.com/id/<id>
# then: kubectl annotate sa edge-agent -n edge \
#   eks.amazonaws.com/role-arn=$(terraform output -raw irsa_role_arn)
```

> CI runs `terraform fmt -check` + `terraform validate` (no credentials/state needed).
> An `apply` needs AWS credentials and a globally-unique bucket name.
