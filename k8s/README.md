# `k8s/` — Kubernetes manifests (k3s local → EKS portable)

The whole edge-to-cloud stack as Kubernetes objects. Designed to run on a single-node
**k3s** cluster locally and to lift onto **EKS** with only the object-store swap.

## What's in here
| File | Object | Role |
|------|--------|------|
| `00-namespace.yaml` | Namespace `edge` | isolation |
| `10-minio.yaml` | Deployment + PVC + Service + init Job | S3-compatible object store (S3 stand-in) |
| `20-agent.yaml` | **DaemonSet** + ConfigMap + headless Service | one capture agent per node (= per edge device); `DEVICE_ID` from node name; Prometheus scrape annotations; liveness/readiness probes |
| `30-prometheus.yaml` | Deployment + RBAC + ConfigMap | pod-discovery scraping + the fleet alert rules |
| `40-grafana.yaml` | Deployment + Service (NodePort 30300) | auto-provisioned datasource + the Edge Fleet dashboard |
| `50-pipeline-cronjob.yaml` | **CronJob** (*/15m) | extract + `dbt build` over landed data |
| `60-logging.yaml` | **Loki** Deployment + **Promtail** DaemonSet + Service | log aggregation (the "ELK" half); ships pod stdout to Grafana's Loki datasource |
| `kustomization.yaml` | — | ties it together; generates the dashboard ConfigMap from `observability/.../fleet.json` |

## Run on k3s
```bash
# 1. build the two images and import them into k3s' containerd
docker build -t edge-sensor-platform/agent:latest    agent/
docker build -t edge-sensor-platform/pipeline:latest pipeline/
docker save edge-sensor-platform/agent:latest    | sudo k3s ctr images import -
docker save edge-sensor-platform/pipeline:latest | sudo k3s ctr images import -

# 2. apply everything
kubectl apply -k k8s/

# 3. watch the fleet come up
kubectl -n edge get pods,ds,cronjob
kubectl -n edge port-forward svc/grafana 3000:3000     # or http://<node>:30300
```
Scale the "fleet" by adding nodes — the DaemonSet schedules a fresh agent on each, and
Prometheus discovers it automatically.

## Portability to EKS
The manifests are vanilla Kubernetes — `kubectl apply -k` works unchanged on EKS. To go
fully managed:
- **Object store**: delete `10-minio.yaml`; point `S3_ENDPOINT` at real S3 (or drop it and
  use the default AWS endpoint) and grant the agent/pipeline an **IRSA** role instead of the
  static `object-store` secret. The bucket + least-priv IAM + IRSA role are provisioned in
  [`../terraform/`](../terraform/README.md).
- **Metrics**: replace the self-hosted Prometheus with the `kube-prometheus-stack` Helm
  chart or **Amazon Managed Prometheus**; the pod annotations stay the same.
- **Scheduling**: the CronJob runs as-is; or trigger it from EventBridge → a Glue/dbt job.

Mapping recap: **MinIO→S3, DaemonSet→edge fleet, CronJob→EventBridge-scheduled job,
Prometheus→AMP/CloudWatch, k3s→EKS**.

> CI validates that these manifests render via `kustomize build` on every push
> (see [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)). A live apply needs a cluster
> + the two images built above.
