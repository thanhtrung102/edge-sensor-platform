# End-to-End Run — captured results

A full run of the platform on a single machine (Windows, native processes — no Docker, no
cloud account), exercising every stage with **real data at scale**. Run date: 2026-06-05.

> Reproduce: `docker compose up -d --build` then `docker compose run --rm pipeline` (Docker),
> or the native commands in the [README](../README.md). CI re-runs the pipeline + tests on every push.

## What this run also fixed (found by running at scale)
Running end-to-end against a bucket of **~85,000 objects** surfaced two real bugs, both fixed in
[`pipeline/extract.py`](../pipeline/extract.py):

1. **Memory blow-up.** The original extract used 32 worker threads and materialized *all* object
   futures/bodies at once → on this 8 GB box it spiked free RAM to ~2 MB and the OS rebooted.
   Fix: **bounded worker pool + chunked processing** (`EXTRACT_WORKERS`, `EXTRACT_CHUNK`,
   `EXTRACT_MAX_OBJECTS`). Re-run footprint: the extract process held steady at **~68 MB** while
   processing 20 k objects.
2. **Corrupt-object crash.** One sensor object was an all-null-byte truncated body (a partial
   upload left when the machine rebooted mid-write); `json.loads` aborted the whole run. Fix:
   **`fetch` skips + counts unreadable objects** instead of failing the batch. The re-run skipped
   **2** corrupt objects and completed cleanly.

## Stage-by-stage evidence

| Stage | Component | Result |
|------|-----------|--------|
| 1 Capture | Edge agent (`:8000`) | live, `backend_online: true`; ~2 fps synthetic camera + 3 sensors |
| 2 Store | MinIO (`:9000/9001`) | **42,771** sensor objects (+~42 k frames) under `edge-001/{frames,sensors}/Y/M/D/H/`; capture-time partitioning verified (`…/10/sensors_20260605T1030…`) |
| 3 Metrics | Prometheus (`:9090`) | `up{job="edge-agents"}=1`, agent metrics scraped (incl. new `edge_buffer_evicted_total`) |
| 4 Logs | Loki + Promtail (`:3100/:9080`) | events indexed: `agent_starting, backend_reconnected, sensor_anomaly, upload_batch` |
| 5 Visualize | Grafana (`:3000`) | provisioned Prometheus+Loki datasources + *Edge Fleet* dashboard; live values — Agents up **1**, Anomalies/5m **60**, Upload backlog **22**, Seconds-since-capture **2.20s** (see screenshot) |
| 6 Analytics | dbt + DuckDB | extract **20 k** objects in 70 s (2 corrupt skipped) → `dbt build` **14 PASS** in 16 s |

![Edge Fleet dashboard](../observability/grafana-dashboard.png)

## Pipeline output (marts, real 20 k-object run)

**`mart_capture_health`** — realized capture completeness per hour:

| hour (UTC) | readings | frames | expected | capture_rate_% |
|---|---|---|---|---|
| 08:00 | 4090 | 7176 | 7200 | 56.8 |
| 09:00 | 7126 | 7126 | 7200 | **99.0** |
| 10:00 | 4168 | 4170 | 7200 | 57.9 |
| 12:00 | 4614 | 4712 | 7200 | 64.1 |

**`mart_sensor_quality`** — e.g. temperature_c avg ≈ 44.6 °C, max 187 (injected spikes);
humidity max ≈ 240; vibration max ≈ 2.1. (`assert_no_missing_sensor_values` → 0 nulls.)

**`mart_anomalies`** — **1,741** readings flagged by robust z-score (median+MAD), ~8.7 % of
readings ≈ `ANOMALY_RATE 0.03 × 3 sensors`; max |z| ≈ 67. Independently recovers the spikes the
agent injects, reconciling batch detection against the live `edge_sensor_anomaly_total` metric.

## Final state
All six services healthy (HTTP 200): agent · MinIO · Prometheus · Grafana · Loki · Promtail.

## Notes / constraints
- This 8 GB box can run the full stack **or** a full-bucket extract, but the heavy extract should
  not run alongside peak observability load — that's what the memory fix addresses (now bounded to
  ~68 MB) and why this run capped the extract at 20 k objects. On real hardware/EKS this is a non-issue.
- The dashboard screenshot's stat panels show live values; the time-series panels render their
  series in the live UI (http://localhost:3000) — headless capture occasionally clips slow panels.
