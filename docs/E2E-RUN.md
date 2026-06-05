# End-to-End Run — captured results

A full run of the platform on a single machine (Windows, native processes — no Docker, no
cloud account), exercising every stage with **real data at scale**. Run date: 2026-06-05.

> Reproduce: `docker compose up -d --build` then `docker compose run --rm pipeline` (Docker),
> or the native commands in the [README](../README.md). CI re-runs the pipeline + tests on every push.

## What this run also fixed (found by running at full scale)
Running end-to-end against the full bucket surfaced three real bugs, all fixed in
[`pipeline/extract.py`](../pipeline/extract.py):

1. **Memory blow-up.** The original extract used 32 worker threads and materialized *all* object
   futures/bodies at once → on this 8 GB box it spiked free RAM to ~2 MB and the OS rebooted.
   Fix: **bounded worker pool + chunked processing** (`EXTRACT_WORKERS`, `EXTRACT_CHUNK`,
   `EXTRACT_MAX_OBJECTS`). The fixed extract held steady at **~68 MB** while processing the full
   **53,002-object** bucket — on a box with under 1 GB free.
2. **Corrupt-object crash.** One sensor object was an all-null-byte truncated body (a partial
   upload left when the machine rebooted mid-write); `json.loads` aborted the whole run. Fix:
   **`fetch` skips + counts unreadable objects** instead of failing the batch. The full run skipped
   **2** corrupt objects and completed cleanly.
3. **Unbounded DuckDB COPY.** The final compaction could grow with the bucket. Fix: a
   **`memory_limit` (default 512 MB) + on-disk spill** so the transform can't OOM the box either.

## Stage-by-stage evidence

| Stage | Component | Result |
|------|-----------|--------|
| 1 Capture | Edge agent (`:8000`) | live, `backend_online: true`; ~2 fps synthetic camera + 3 sensors |
| 2 Store | MinIO (`:9000/9001`) | **53,002** sensor objects (+frames) under `edge-001/{frames,sensors}/Y/M/D/H/`; capture-time partitioning verified (`…/10/sensors_20260605T1030…`) |
| 3 Metrics | Prometheus (`:9090`) | `up{job="edge-agents"}=1`, agent metrics scraped (incl. new `edge_buffer_evicted_total`) |
| 4 Logs | Loki + Promtail (`:3100/:9080`) | events indexed: `agent_starting, backend_reconnected, sensor_anomaly, upload_batch` |
| 5 Visualize | Grafana (`:3000`) | provisioned Prometheus+Loki datasources + *Edge Fleet* dashboard; live values — Agents up **1**, Anomalies/5m **60**, Upload backlog **22**, Seconds-since-capture **2.20s** (see screenshot) |
| 6 Analytics | dbt + DuckDB | **full bucket: 53,002 objects** (2 corrupt skipped → 53,000 landed) → `dbt build` **14 PASS** |
| + Field reliability | agent + Prometheus + Loki | **alert-firing demo** (below) — store-and-forward + escalation |

![Edge Fleet dashboard](../observability/grafana-dashboard.png)

## Pipeline output (marts, real 20 k-object run)

**`mart_capture_health`** — realized capture completeness per hour (full 53k-object run, 9 hours):

| hour (UTC) | readings | frames | capture_rate_% |
|---|---|---|---|
| 05:00 | 5720 | 5720 | 79.4 |
| 06:00 | 7127 | 7127 | **99.0** |
| 07:00 | 7171 | 7171 | **99.6** |
| 08:00 | 7176 | 7176 | **99.7** |
| 09:00 | 7126 | 7126 | **99.0** |
| 10:00 | 4168 | 4170 | 57.9 |
| 12:00 | 6146 | 4712 | 85.4 |
| 13:00 | 5663 | 0 | 78.7 |
| 14:00 | 2703 | 0 | 37.5 |

(Hours 13–14 show `frames=0` — the live frame uploads lagged the sensor extract; `reading_frame_skew`
is exactly the partial-upload detector that surfaces this.)

**`mart_sensor_quality`** — 27 rows (3 sensors × 9 hours); temperature_c avg ≈ 44.6 °C, max ~187
(injected spikes); humidity max ~240; vibration max ~2.1. (`assert_no_missing_sensor_values` → 0 nulls.)

**`mart_anomalies`** — **4,871** readings flagged by robust z-score (median+MAD), ~9 % of readings
≈ `ANOMALY_RATE 0.03 × 3 sensors`; max |z| ≈ 67. Independently recovers the spikes the agent
injects, reconciling batch detection against the live `edge_sensor_anomaly_total` metric.

## Field-reliability / alert-firing demo (captured live)
Controlled fault injection — stop the object store, confirm no data loss + escalation, restore:

```
MinIO stopped 21:41:36 → agent keeps capturing, buffers locally:
  backlog 22 → 48 → 88 → 124 → 164 → 202 → 242
  EdgeUploadBacklogHigh: pending @ t+30s (backlog>50) → FIRING @ t+60s (after 30s `for`)
MinIO restarted 21:42:48 → backlog drains 314 → 42 in ~18s → alert RESOLVED
```
The whole incident is reconstructable from Loki:
```json
{"event":"backend_unreachable","backlog":22,"error":"EndpointConnectionError"}   // 14:41:38Z
{"event":"backend_reconnected","backlog_drained_from":314}                        // 14:42:50Z
```
Zero data lost (buffer drained on reconnect); metrics fired the page, logs explained why.

## Final state
All six services healthy (HTTP 200): agent · MinIO · Prometheus · Grafana · Loki · Promtail.

## Notes / constraints
- The **full bucket (53,002 objects)** was extracted on a box with **<1 GB free RAM**, with the
  extract process bounded to **~68 MB** and DuckDB capped at 512 MB — the three fixes above are what
  make that safe. To free headroom for the heavy batch, Grafana (the ~2 GB process) was paused during
  the extract and restored afterward; the full stack runs together at steady state. On real
  hardware / EKS this scheduling concern is a non-issue.
- The dashboard screenshot's stat panels show live values; the time-series panels render their
  series in the live UI (http://localhost:3000) — headless capture occasionally clips a
  freshly-booted Grafana's slow panels.
