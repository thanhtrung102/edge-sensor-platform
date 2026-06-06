# Demo runbook — Edge-to-Cloud Sensor & Camera Platform

A 5–8 minute live walkthrough for a stakeholder/interviewer. The story: **a fleet of edge
devices capturing camera + sensor data, shipped reliably to cloud storage, observed as a fleet,
and analyzed — built to map 1:1 onto AWS** (MinIO→S3, k3s→EKS, Prometheus/Grafana→CloudWatch,
Loki→CloudWatch Logs, dbt-duckdb→Glue/Athena).

## 0. Bring it up (one command)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\demo-up.ps1     # waits until all 6 services are healthy
# stop later:  powershell -ExecutionPolicy Bypass -File scripts\demo-down.ps1
```
(Docker alternative — portable: `docker compose up -d --build`.)

## The 6 services and the URLs
| Service | URL | Login |
|---|---|---|
| Grafana (start here) | http://localhost:3000 | admin / admin |
| Agent health / metrics | http://localhost:8000/healthz · /metrics | — |
| MinIO console | http://localhost:9001 | minioadmin / minioadmin |
| Prometheus alerts | http://localhost:9090/alerts | — |

---

## Demo script (what to click + what to say)

### 1. The edge agent (capture) — 1 min
- Open **http://localhost:8000/healthz** → point out `frames_captured`, the two backlogs
  (`backlog_telemetry` vs `backlog_recordings`), `backend_online`.
- **Say:** "Each device runs this agent — it captures camera frames + 3 sensors at 2 fps,
  buffers to local disk first, and ships to object storage. It's the autonomous, offline-tolerant
  daemon you'd run one-per-robot as a Kubernetes DaemonSet."

### 2. Object storage + the two planes (storage) — 1 min
- Open **MinIO console** → bucket `edge-data` → drill into `edge-001/`.
- Show **`recordings/Y/M/D/H/seg_*.mcap`** and **`sensors/Y/M/D/H/*.json`**.
- **Say:** "Two planes, like real robot fleets: heavy camera frames go into **rotated MCAP
  segments** — the ROS 2 / Foxglove bag standard, one self-describing object instead of hundreds
  of tiny JPEGs — and small sensor scalars stay as JSON. Note the `Y/M/D/H` partitioning is by
  **capture time**, so data buffered through an outage still lands in the right hour."

### 3. Fleet observability (metrics + logs) — 1.5 min
- **Grafana → Edge Fleet dashboard**: agents up, capture rate, upload backlog, anomalies, skew.
- **Grafana → Explore → Loki** datasource → `{job="edge-agent"} | json` → filter `event="segment_sealed"`
  or `event="sensor_anomaly"`.
- **Say:** "Metrics answer *what/when* (Prometheus→Grafana), logs answer *why* (Loki) — the
  open-source ELK + CloudWatch story. This is the operability half of the job."

### 4. Field-reliability / alerting — the money demo — 2 min
Controlled fault injection — stop the object store, show no data loss + escalation, restore:
```powershell
Get-Process minio | Stop-Process -Force          # object store "goes offline"
# watch: agent keeps capturing; edge_upload_backlog_files climbs;
#        Prometheus EdgeUploadBacklogHigh -> pending (>50) -> FIRING after 30s
powershell -File scripts\demo-up.ps1             # or just restart minio -> backlog drains, alert RESOLVES
```
- Show **http://localhost:9090/alerts** going pending→firing, then resolved.
- Show Loki: `event="backend_unreachable"` then `event="backend_reconnected"` with `backlog_drained_from`.
- **Say:** "Zero data lost — the buffer drained on reconnect into the correct partitions. Metrics
  paged the operator; logs explained the incident. That's the on-site-ops reliability story."

### 5. Analytics marts (pipeline) — 1.5 min
Query the already-built marts (instant — good for a live demo):
```powershell
cd pipeline
..\.venv\Scripts\python -c "import duckdb; c=duckdb.connect('edge.duckdb', read_only=True); [print(r) for r in c.execute('select capture_hour, readings, frames, capture_rate_pct from mart_capture_health order by capture_hour').fetchall()]; print('anomalies:', c.execute('select count(*) from mart_anomalies').fetchone()[0])"
```
Rebuild from storage (optional — **note:** `extract.py` lists the whole bucket, so on a large
historical bucket this takes a few minutes; bound it for a quick rebuild):
```powershell
$env:EXTRACT_MAX_OBJECTS="4000"; ..\.venv\Scripts\python extract.py   # compact + read MCAP -> parquet
..\.venv\Scripts\dbt build --profiles-dir .                           # build + test marts (expect: all PASS)
```
- Show the three marts (`mart_capture_health`, `mart_sensor_quality`, `mart_anomalies`).
- **Say:** "dbt + DuckDB over the landed objects — the open-source analogue of Glue/Athena. It
  recovers per-hour capture completeness, sensor-quality stats, and — via a robust z-score —
  **independently re-discovers the anomalies** the agent injected, reconciling batch analytics
  against the live metric. Frames are counted *from inside the MCAP segments*."

## The one-line value
> One agent per device captures and resiliently ships robot/sensor telemetry; the platform stores
> it durably (partition-correct, small-files solved at source via MCAP), observes the fleet
> (metrics + logs + alerts), and analyzes it (dbt marts) — all open-source, all mapping 1:1 to AWS,
> verified end-to-end on a single machine.

## Reset / safety
- `scripts\demo-down.ps1` stops everything; **data is preserved** (pause, not wipe).
- All synthetic by default (`CAMERA_SOURCE=synthetic`). `CAMERA_SOURCE=webcam` uses a real USB camera.
