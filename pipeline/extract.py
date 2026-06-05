"""Landing extract: compact raw edge objects from S3-compatible storage into parquet.

Edge agents write one tiny JSON per capture (the classic small-files problem). This
job lists the sensor/ and frames/ prefixes, pulls the sensor JSON bodies concurrently,
and writes two compacted parquet files into the dbt landing zone:

    landing/sensor_readings.parquet   one row per capture (device, site, ts, sensor values)
    landing/frame_index.parquet       one row per frame object (device key, size) - keys only

dbt-duckdb then builds marts on top. Mirrors a real "land raw -> transform" pipeline and
keeps duckdb off the slow per-object HTTP path (it reads local parquet instead).

Env: S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, DEVICE_PREFIX (optional).
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
import duckdb
from botocore.config import Config
from botocore.exceptions import ClientError

ENDPOINT = os.getenv("S3_ENDPOINT", "http://127.0.0.1:9000")
BUCKET = os.getenv("S3_BUCKET", "edge-data")
KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
SECRET = os.getenv("S3_SECRET_KEY", "minioadmin")
PREFIX = os.getenv("DEVICE_PREFIX", "")  # "" = all devices
# Bounded concurrency + chunking so a very large bucket can't blow up memory:
# we keep at most CHUNK keys' futures and WORKERS in-flight bodies at a time.
WORKERS = int(os.getenv("EXTRACT_WORKERS", "8"))
CHUNK = int(os.getenv("EXTRACT_CHUNK", "2000"))
MAX_OBJECTS = int(os.getenv("EXTRACT_MAX_OBJECTS", "0"))  # 0 = no cap; else most-recent N
LANDING = Path(__file__).parent / "landing"
LANDING.mkdir(exist_ok=True)


def _client():
    return boto3.client(
        "s3", endpoint_url=ENDPOINT, aws_access_key_id=KEY,
        aws_secret_access_key=SECRET, region_name="us-east-1",
        config=Config(retries={"max_attempts": 3}, max_pool_connections=64),
    )


def _list(s3, suffix: str):
    """Yield (key, size) for every object whose key contains /{suffix}/."""
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            if f"/{suffix}/" in obj["Key"]:
                yield obj["Key"], obj["Size"]


def main():
    s3 = _client()

    # --- sensor readings: fetch JSON bodies concurrently, stage as NDJSON ---
    sensor_keys = [k for k, _ in _list(s3, "sensors")]
    if MAX_OBJECTS:
        sensor_keys = sorted(sensor_keys)[-MAX_OBJECTS:]  # most-recent N (keys sort chronologically)
    print(f"sensor objects: {len(sensor_keys)} (workers={WORKERS}, chunk={CHUNK})", flush=True)
    ndjson = LANDING / "_sensors.ndjson"

    def fetch(key):
        # Tolerate corrupt/truncated objects (e.g. a partial upload left by a crash mid-write):
        # skip them rather than aborting the whole run. Returns None on any read/parse failure.
        try:
            body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
            rec = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, ClientError):
            return None
        rec["s3_key"] = key
        return json.dumps(rec)

    # Process in bounded chunks with a capped worker pool so we never materialize all
    # object bodies / futures at once — the bucket can hold tens of thousands of objects.
    written = skipped = 0
    with ndjson.open("w", encoding="utf-8") as fh:
        for i in range(0, len(sensor_keys), CHUNK):
            chunk = sensor_keys[i:i + CHUNK]
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                for line in pool.map(fetch, chunk):
                    if line is None:
                        skipped += 1
                        continue
                    fh.write(line + "\n")
                    written += 1
            print(f"  fetched {min(i + CHUNK, len(sensor_keys))}/{len(sensor_keys)}", flush=True)
    if skipped:
        print(f"skipped {skipped} corrupt/unreadable objects", flush=True)

    con = duckdb.connect()
    sensors_pq = (LANDING / "sensor_readings.parquet").as_posix()
    if sensor_keys:
        con.execute(
            f"COPY (SELECT * FROM read_json_auto('{ndjson.as_posix()}', union_by_name=true)) "
            f"TO '{sensors_pq}' (FORMAT parquet)"
        )
    print(f"wrote sensor_readings.parquet ({len(sensor_keys)} rows)", flush=True)
    ndjson.unlink(missing_ok=True)

    # --- frame index: keys + sizes only (no body download needed) ---
    frames = list(_list(s3, "frames"))
    frames_ndjson = LANDING / "_frames.ndjson"
    with frames_ndjson.open("w", encoding="utf-8") as fh:
        for k, sz in frames:
            fh.write(json.dumps({"s3_key": k, "size_bytes": sz}) + "\n")
    frames_pq = (LANDING / "frame_index.parquet").as_posix()
    if frames:
        con.execute(
            f"COPY (SELECT * FROM read_json_auto('{frames_ndjson.as_posix()}')) "
            f"TO '{frames_pq}' (FORMAT parquet)"
        )
    print(f"wrote frame_index.parquet ({len(frames)} rows)", flush=True)
    frames_ndjson.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
