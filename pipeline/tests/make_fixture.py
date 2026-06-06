"""Generate tiny DETERMINISTIC landing parquet so `dbt build` can run in CI without MinIO,
and so repeated runs produce byte-identical marts (reproducibility).

Writes the two files extract.py would produce (sensor_readings.parquet, frame_index.parquet)
for a single device / one hour, schema-identical to the real extract output. `setseed` pins the
RNG so the values are stable across runs; two deterministic temperature spikes (s = 17, 43) make
mart_anomalies non-empty and stable, so the anomaly detector's output is verifiable run-to-run.
"""
from pathlib import Path

import duckdb

LANDING = Path(__file__).resolve().parents[1] / "landing"
LANDING.mkdir(exist_ok=True)
con = duckdb.connect()
con.execute("SELECT setseed(0.42)")  # pin the RNG -> reproducible sensor values

con.execute(
    f"""
    COPY (
        SELECT
            'edge-001' AS device_id,
            'hanoi-lab' AS site,
            TIMESTAMP '2026-01-01 09:00:00' + (s * INTERVAL 1 SECOND) AS ts,
            -- two deterministic spikes (s=17,43) so the robust-z anomaly mart has stable, non-zero output
            round((42 + random() * 3) * CASE WHEN s IN (17, 43) THEN 3.5 ELSE 1 END, 3) AS temperature_c,
            round(0.5 + random() * 0.1, 3) AS vibration_g,
            round(55 + random() * 4, 3) AS humidity_pct,
            'edge-001/sensors/2026/01/01/09/sensors_20260101T0900' || lpad(s::VARCHAR, 2, '0') || '.json' AS s3_key
        FROM range(0, 60) t(s)
    ) TO '{(LANDING / 'sensor_readings.parquet').as_posix()}' (FORMAT parquet)
    """
)
con.execute(
    f"""
    COPY (
        SELECT
            'edge-001/recordings/2026/01/01/09/seg_20260101T090000000000.mcap' AS segment_key,
            TIMESTAMP '2026-01-01 09:00:00' + (s * INTERVAL 1 SECOND) AS captured_at,
            15000 AS size_bytes
        FROM range(0, 60) t(s)
    ) TO '{(LANDING / 'frame_index.parquet').as_posix()}' (FORMAT parquet)
    """
)
print("fixture written (deterministic, setseed=0.42):", LANDING)
