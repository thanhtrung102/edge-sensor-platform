# Deterministic reproduce — Edge-to-Cloud Sensor & Camera Platform

**Problem it solves:** reliably collect camera + sensor telemetry from edge/field devices to cloud
storage, observe the fleet, detect anomalies, and survive network outages with zero data loss.

There are two reproducible layers. The **analytical** layer is fully deterministic (identical output
every run, CI-enforced). The **operational** layer is a real-time stack you bring up with one command.

---

## A. Analytical pipeline — DETERMINISTIC, byte-identical every run
Proves the pipeline recovers the injected anomalies and builds the marts reproducibly. No MinIO/AWS.

```powershell
cd pipeline
..\.venv\Scripts\python tests\make_fixture.py      # seeded fixture (setseed=0.42 + 2 fixed spikes)
..\.venv\Scripts\dbt build --profiles-dir .         # build + test the marts
```

**Expected output — identical on every run:**
- `dbt build` → **PASS=14 WARN=0 ERROR=0** (3 models, 3 views, 8 tests)
- `mart_capture_health` → **1 row** (hour 09, 60 readings / 60 frames)
- `mart_sensor_quality` → **3 rows** (3 sensors × 1 hour)
- `mart_anomalies` → **exactly 2 rows** — the two seeded temperature spikes (s=17, s=43), recovered
  by the robust z-score (median + MAD). This is the detector "serving its purpose," deterministically.

**Determinism check (run it twice, compare):**
```powershell
# both runs print identical SHA-256 over the sorted mart rows
..\.venv\Scripts\python -c "import duckdb,hashlib; c=duckdb.connect('edge.duckdb',read_only=True); print({m: hashlib.sha256(repr(c.execute(f'select * from {m} order by 1,2,3').fetchall()).encode()).hexdigest()[:16] for m in ['mart_capture_health','mart_sensor_quality','mart_anomalies']})"
# verified: {'mart_capture_health':'8fc7312f2d0b2b14','mart_sensor_quality':'ca55f8961fee24b7','mart_anomalies':'f0fe8143c511cee1'}
```
The same `make_fixture.py` + `dbt build` runs in CI on every push (`.github/workflows/ci.yml`).

---

## B. Operational stack — one-command bring-up (real-time E2E)
Proves capture → store-and-forward → object storage → observability → alerting end to end.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\demo-up.ps1     # all 6 services, waits for health
# Grafana http://localhost:3000 (admin/admin) · Agent http://localhost:8000/healthz
# MinIO http://localhost:9001 (minioadmin/minioadmin) · Prometheus http://localhost:9090/alerts
powershell -ExecutionPolicy Bypass -File scripts\demo-down.ps1   # graceful stop (data preserved)
```

**Repeatable field-reliability demo (the purpose under failure):**
```powershell
Get-Process minio | Stop-Process -Force      # object store offline -> backlog climbs, alert FIRES
powershell -File scripts\demo-up.ps1         # restored -> backlog drains, alert RESOLVES, zero loss
```
Reconstruct the incident in Loki (Grafana → Explore): `{job="edge-agent"} | json` →
`backend_unreachable` then `backend_reconnected` with `backlog_drained_from`.

**Full live analytics (optional, larger run):** bound the extract so it doesn't list the whole
historical bucket: `cd pipeline; $env:EXTRACT_MAX_OBJECTS="4000"; ..\.venv\Scripts\python extract.py; ..\.venv\Scripts\dbt build --profiles-dir .`

## Determinism notes
- Layer A is deterministic by construction (seeded fixture). Layer B is a live real-time system, so
  exact counts vary; its *reliability behavior* (buffer→alert→drain→zero-loss) is what's reproducible.
- Cloud target: MinIO→S3, k3s→EKS, Prometheus/Grafana→CloudWatch, dbt-duckdb→Glue/Athena (see README).
