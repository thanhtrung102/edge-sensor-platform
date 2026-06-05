"""Generate tiny deterministic landing parquet so `dbt build` can run in CI without MinIO.

Writes the same two files extract.py would produce (sensor_readings.parquet, frame_index.parquet)
with a single device, one hour of data, schema-identical to the real extract output.
"""
from pathlib import Path

import duckdb

LANDING = Path(__file__).resolve().parents[1] / "landing"
LANDING.mkdir(exist_ok=True)
con = duckdb.connect()

con.execute(
    f"""
    COPY (
        SELECT
            'edge-001' AS device_id,
            'hanoi-lab' AS site,
            TIMESTAMP '2026-01-01 09:00:00' + (s * INTERVAL 1 SECOND) AS ts,
            round(42 + random() * 3, 3) AS temperature_c,
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
print("fixture written:", LANDING)
