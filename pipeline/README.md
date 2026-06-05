# `pipeline/` — dbt-duckdb analytics over landed edge data

Transforms the raw objects the edge agents upload to S3-compatible storage into
analytics marts, using **dbt + DuckDB** (no warehouse, no cloud account). This is the
open-source analogue of a Glue/Athena transform layer: `MinIO→S3`, `DuckDB→Athena`,
`dbt models→dbt-on-Athena`.

## Flow
```
S3 (edge-data/{device}/sensors|frames/...)            many tiny JSON/JPEG objects
        │  extract.py  (boto3, 32-way concurrent, compaction)
        ▼
landing/sensor_readings.parquet   landing/frame_index.parquet
        │  dbt run   (DuckDB)
        ▼
staging:  stg_sensor_readings · stg_sensor_long · stg_frames
marts:    mart_capture_health · mart_sensor_quality · mart_anomalies
```

`extract.py` deliberately **compacts** thousands of small objects into parquet — both a
real-world fix for the small-files problem and a way to keep DuckDB off the slow
per-object HTTP path.

## Marts
- **mart_capture_health** — per device/hour: readings & frames landed vs the expected
  count from `CAPTURE_FPS`, a `capture_rate_pct`, and `reading_frame_skew` (gap between
  sensor and frame uploads → partial-upload detector).
- **mart_sensor_quality** — per device/sensor/hour: count, mean, median, stddev, range,
  null count. Catches drift / stuck sensors. (`assert_no_missing_sensor_values` enforces 0 nulls.)
- **mart_anomalies** — readings whose **robust z-score** (median + MAD) exceeds
  `var('anomaly_sigma')`. Independently recovers the spikes the agent injects, so you can
  reconcile batch detection against the live `edge_sensor_anomaly_total` metric.

## Run
```bash
# 1. land raw objects (uses S3_ENDPOINT / S3_BUCKET / S3_ACCESS_KEY / S3_SECRET_KEY)
python extract.py
# 2. build + test
dbt run  --profiles-dir .
dbt test --profiles-dir .
# 3. inspect
duckdb edge.duckdb -c "select * from mart_capture_health order by capture_hour"
```

Verified end-to-end against the live MinIO from the running stack: 11,556 readings landed,
6 models built, 8 tests pass, 1,040 anomalies recovered.
