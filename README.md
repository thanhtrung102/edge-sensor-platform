# Edge-to-Cloud Sensor & Camera Data-Collection Platform

A robotics/manufacturing-style **edge-to-cloud** telemetry pipeline: edge agents capture
**camera frames + sensor readings**, buffer them to local disk for **field/offline reliability**,
upload to **S3-compatible object storage** with retry, and surface **fleet health + alerts** through a
**Prometheus/Grafana** observability stack. Built to mirror the edge + cloud + observability + on-site-ops
shape of a real sensor-data-collection operation — and to run **entirely locally, free, with no cloud account**.

> Designed as an open-source stack that maps 1:1 to AWS: **MinIO→S3, k3s→EKS, Prometheus/Grafana→CloudWatch**.

## Architecture
```
 EDGE (hardware)                 CLOUD / PIPELINE              OBSERVABILITY
 Edge Agent (FastAPI)            MinIO (S3 object store)       Prometheus ─┐
  • camera (synthetic|webcam)     edge-data/                    Grafana  ◀─┘ (fleet dashboard)
  • sensors + anomaly inject  ──▶  {device}/frames/...     ──▶  Alertmanager-ready rules:
  • local ring-buffer (retry)      {device}/sensors/...          stall · backlog · anomaly · agent-down
  • /metrics + /healthz
```

## Quick start (Docker — zero hardware)
```bash
cp .env.example .env
docker compose up -d --build
```
Then open:
- **Agent health:** http://localhost:8000/healthz   ·   **metrics:** http://localhost:8000/metrics
- **MinIO console:** http://localhost:9001  (minioadmin / minioadmin) — watch objects land under `edge-data/`
- **Prometheus:** http://localhost:9090  (try `rate(edge_frames_captured_total[1m])`; Alerts tab)
- **Grafana:** http://localhost:3000  (admin / admin) → dashboard **"Edge Fleet — Capture & Health"**

## Run natively (no Docker — verified on Windows)
If Docker isn't available, every component runs as a native process. This exact path was verified
end-to-end (agent → MinIO, Prometheus scraping, Grafana dashboard):
```bash
# 1. Agent (venv)
python -m venv .venv && ./.venv/Scripts/python -m pip install -r agent/requirements.txt
S3_ENDPOINT=http://127.0.0.1:9000 S3_BUCKET=edge-data BUFFER_DIR=./buf \
  ./.venv/Scripts/python -m uvicorn agent:app --app-dir agent --port 8000
# 2. MinIO (single binary)        → bin/minio.exe server ./miniodata --console-address :9001
# 3. Prometheus (single binary)   → prometheus --config.file=observability/prometheus.local.yml
# 4. Grafana 13 (grafana.exe)     → GF_PATHS_PROVISIONING=observability/grafana/provisioning-local grafana.exe server
```
`prometheus.local.yml` and `grafana/provisioning-local/` target `127.0.0.1` (vs the Docker DNS names in
the compose path). Sample rendered dashboard: `observability/grafana-dashboard.png`.

![Edge Fleet dashboard](observability/grafana-dashboard.png)

## Use a real USB camera
The default is synthetic capture (works everywhere). For a real webcam, run the agent **natively**
(host webcam isn't reachable from a Linux container on Windows/macOS):
```bash
pip install -r agent/requirements.txt opencv-python-headless
CAMERA_SOURCE=webcam S3_ENDPOINT=http://localhost:9000 BUFFER_DIR=./buf \
  uvicorn agent:app --app-dir agent --port 8000
```

## Demonstrate field reliability (offline buffering)
```bash
docker compose stop minio        # simulate the object store going offline
# → agent keeps capturing; edge_upload_backlog_files climbs; EdgeUploadBacklogHigh alert fires
docker compose start minio       # backlog drains automatically on reconnect
```

## What this demonstrates
- **Data pipeline / object storage** (S3-compatible, partitioned by device/date/hour)
- **Observability stack** (Prometheus metrics, Grafana dashboards, alert rules incl. a dead-man's-switch)
- **On-site ops automation** (self-health, auto-restart, offline-tolerant retry, escalation alerts)
- **Cameras/sensors + local storage** (USB-camera-ready, sensor telemetry, local ring-buffer)
- **DevOps**: containerized; Kubernetes (k3s/EKS) manifests + CI/CD are the next layer (`k8s/`, planned)

## Roadmap
- `pipeline/` — dbt-duckdb over uploaded data → capture-rate / quality / anomaly marts
- `k8s/` — k3s manifests (agent as DaemonSet, CronJobs for dbt) → portable to EKS
- Loki/Promtail for log aggregation (the "ELK" half of the observability requirement)
