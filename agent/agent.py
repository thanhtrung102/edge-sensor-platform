"""Edge capture agent — TechValley-style edge-to-cloud sensor/camera data collection.

Captures camera frames + sensor readings at the edge, buffers them to local disk
(field/offline reliability), uploads to S3-compatible object storage with retry,
and exposes Prometheus metrics + a health endpoint for on-site ops monitoring.

Runs anywhere with synthetic capture by default (CAMERA_SOURCE=synthetic);
set CAMERA_SOURCE=webcam to use a real USB camera via OpenCV.
"""
import io
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import numpy as np
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import FastAPI, Response
from PIL import Image, ImageDraw
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

# ----------------------------------------------------------------------------- config
DEVICE_ID = os.getenv("DEVICE_ID", "edge-001")
SITE = os.getenv("SITE", "hanoi-lab")
CAPTURE_FPS = float(os.getenv("CAPTURE_FPS", "2"))
CAMERA_SOURCE = os.getenv("CAMERA_SOURCE", "synthetic")  # synthetic | webcam
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
BUFFER_DIR = Path(os.getenv("BUFFER_DIR", "/data/buffer"))
UPLOAD_INTERVAL = float(os.getenv("UPLOAD_INTERVAL", "5"))
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://minio:9000")
S3_BUCKET = os.getenv("S3_BUCKET", "edge-data")
S3_KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
S3_SECRET = os.getenv("S3_SECRET_KEY", "minioadmin")
SENSORS = ["temperature_c", "vibration_g", "humidity_pct"]
ANOMALY_RATE = float(os.getenv("ANOMALY_RATE", "0.03"))
# Hard cap on the local store-and-forward buffer. A long outage must not fill the disk and
# take the node down; past this size we drop the OLDEST buffered files (newest data wins).
MAX_BUFFER_BYTES = int(os.getenv("MAX_BUFFER_BYTES", str(512 * 1024 * 1024)))  # 512 MiB

BUFFER_DIR.mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------------------- logging
# Structured JSON logs to stdout so Promtail/Loki (or CloudWatch) can index them by
# device/site/event. 12-factor: log to stdout, let the platform ship it.
class _JsonLog(logging.Formatter):
    def format(self, rec):
        out = {
            "ts": datetime.fromtimestamp(rec.created, timezone.utc).isoformat(),
            "level": rec.levelname,
            "device_id": DEVICE_ID,
            "site": SITE,
            "event": rec.getMessage(),
        }
        if isinstance(rec.args, dict):
            out.update(rec.args)
        return json.dumps(out)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_JsonLog())
log = logging.getLogger("edge-agent")
log.setLevel(logging.INFO)
log.addHandler(_handler)
log.propagate = False

# ----------------------------------------------------------------------------- metrics
LBL = {"device_id": DEVICE_ID, "site": SITE}
frames_captured = Counter("edge_frames_captured_total", "Frames captured", ["device_id", "site"])
frames_dropped = Counter("edge_frames_dropped_total", "Frames dropped on capture error", ["device_id", "site"])
uploads = Counter("edge_uploads_total", "Object uploads", ["device_id", "site", "status"])
anomalies = Counter("edge_sensor_anomaly_total", "Sensor anomalies detected", ["device_id", "site", "sensor"])
backlog = Gauge("edge_upload_backlog_files", "Files awaiting upload in local buffer", ["device_id", "site"])
buffer_bytes = Gauge("edge_buffer_disk_bytes", "Local buffer size on disk (bytes)", ["device_id", "site"])
evicted = Counter("edge_buffer_evicted_total", "Buffered files dropped (oldest-first) at buffer cap", ["device_id", "site"])
last_capture = Gauge("edge_last_capture_timestamp_seconds", "Unix ts of last capture", ["device_id", "site"])
last_upload = Gauge("edge_last_successful_upload_timestamp_seconds", "Unix ts of last upload", ["device_id", "site"])
sensor_reading = Gauge("edge_sensor_reading", "Latest sensor reading", ["device_id", "site", "sensor"])

STATE = {"started": time.time(), "online": False, "frames": 0}

# ----------------------------------------------------------------------------- capture
def _synthetic_frame() -> bytes:
    """Generate a frame with a moving marker + timestamp (no hardware needed)."""
    t = time.time()
    arr = (np.random.default_rng(int(t * 10)).integers(0, 40, (240, 320, 3))).astype("uint8")
    img = Image.fromarray(arr)
    d = ImageDraw.Draw(img)
    x = int((t * 40) % 300)
    d.rectangle([x, 100, x + 20, 140], fill=(0, 200, 120))
    d.text((6, 6), f"{DEVICE_ID} {datetime.now(timezone.utc):%H:%M:%S}", fill=(230, 230, 230))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def _webcam_frame():
    import cv2  # imported lazily so synthetic mode needs no OpenCV

    cap = _webcam_frame.cap
    ok, frame = cap.read()
    if not ok:
        return None
    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return jpg.tobytes() if ok else None


def _read_sensors() -> dict:
    rng = np.random.default_rng()
    base = {"temperature_c": 42, "vibration_g": 0.5, "humidity_pct": 55}
    out = {}
    for s in SENSORS:
        val = base[s] + rng.normal(0, base[s] * 0.05)
        if rng.random() < ANOMALY_RATE:  # inject an occasional spike
            val *= rng.uniform(2.5, 4.0)
            anomalies.labels(**LBL, sensor=s).inc()
            log.warning("sensor_anomaly", {"sensor": s, "value": round(float(val), 3)})
        out[s] = round(float(val), 3)
    return out


def capture_loop():
    if CAMERA_SOURCE == "webcam":
        import cv2

        _webcam_frame.cap = cv2.VideoCapture(CAMERA_INDEX)
    interval = 1.0 / CAPTURE_FPS
    while True:
        start = time.time()
        try:
            frame = _synthetic_frame() if CAMERA_SOURCE == "synthetic" else _webcam_frame()
            if frame is None:
                frames_dropped.labels(**LBL).inc()
            else:
                ts = datetime.now(timezone.utc)
                stamp = ts.strftime("%Y%m%dT%H%M%S%f")
                (BUFFER_DIR / f"frame_{stamp}.jpg").write_bytes(frame)
                readings = _read_sensors()
                for s, v in readings.items():
                    sensor_reading.labels(**LBL, sensor=s).set(v)
                (BUFFER_DIR / f"sensors_{stamp}.json").write_text(
                    json.dumps({"device_id": DEVICE_ID, "site": SITE, "ts": ts.isoformat(), **readings})
                )
                frames_captured.labels(**LBL).inc()
                last_capture.labels(**LBL).set(time.time())
                STATE["frames"] += 1
        except Exception:
            frames_dropped.labels(**LBL).inc()
        time.sleep(max(0, interval - (time.time() - start)))


# ----------------------------------------------------------------------------- upload
def _s3():
    return boto3.client(
        "s3", endpoint_url=S3_ENDPOINT, aws_access_key_id=S3_KEY,
        aws_secret_access_key=S3_SECRET, region_name="us-east-1",
        config=Config(retries={"max_attempts": 1}, connect_timeout=3, read_timeout=5),
    )


def _capture_time(path: Path) -> datetime:
    """Recover capture time from the buffered filename (``frame_<stamp>`` / ``sensors_<stamp>``,
    stamp = ``%Y%m%dT%H%M%S%f``). Falls back to file mtime if the name is unexpected.

    Critical for correctness: data buffered during an outage must land in its *capture*-time
    partition, not the *upload*-time partition. Partitioning by upload time silently scatters
    late-arriving data into the wrong hour, breaking time-range queries / Athena partition pruning.
    """
    _, _, stamp = path.stem.partition("_")
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%S%f").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def _key_for(path: Path) -> str:
    kind = "frames" if path.suffix == ".jpg" else "sensors"
    t = _capture_time(path)
    return f"{DEVICE_ID}/{kind}/{t:%Y/%m/%d/%H}/{path.name}"


def _enforce_buffer_cap(files: list) -> list:
    """Drop oldest-first until the buffer is under MAX_BUFFER_BYTES. Returns the survivors.
    `files` must be ordered oldest-first. This bounds disk use during long outages — the
    store-and-forward equivalent of Fluent Bit's filesystem buffer size limit."""
    total = sum(p.stat().st_size for p in files)
    dropped = 0
    while total > MAX_BUFFER_BYTES and dropped < len(files):
        victim = files[dropped]
        try:
            total -= victim.stat().st_size
            victim.unlink(missing_ok=True)
        except OSError:
            pass
        evicted.labels(**LBL).inc()
        dropped += 1
    if dropped:
        log.warning("buffer_cap_evicted", {"evicted": dropped, "cap_bytes": MAX_BUFFER_BYTES})
    return files[dropped:]


def upload_loop():
    while True:
        # oldest-first by mtime so upload order and eviction order are both chronological
        files = sorted((p for p in BUFFER_DIR.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
        files = _enforce_buffer_cap(files)
        backlog.labels(**LBL).set(len(files))
        buffer_bytes.labels(**LBL).set(sum(p.stat().st_size for p in files))
        client = _s3()
        sent = 0
        for path in files:
            try:
                client.upload_file(str(path), S3_BUCKET, _key_for(path))
                path.unlink(missing_ok=True)
                uploads.labels(**LBL, status="success").inc()
                last_upload.labels(**LBL).set(time.time())
                sent += 1
                if not STATE["online"]:
                    log.info("backend_reconnected", {"backlog_drained_from": len(files)})
                STATE["online"] = True
            except (EndpointConnectionError, ClientError, OSError) as exc:
                uploads.labels(**LBL, status="failed").inc()
                if STATE["online"]:
                    log.warning("backend_unreachable", {"backlog": len(files), "error": type(exc).__name__})
                STATE["online"] = False
                break  # backend down — keep buffer, retry next cycle (offline tolerance)
        if sent:
            log.info("upload_batch", {"uploaded": sent, "remaining_backlog": len(files) - sent})
        time.sleep(UPLOAD_INTERVAL)


# ----------------------------------------------------------------------------- app
app = FastAPI(title=f"Edge Agent {DEVICE_ID}")


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
def healthz():
    pending = len([p for p in BUFFER_DIR.iterdir() if p.is_file()])
    return {
        "device_id": DEVICE_ID, "site": SITE, "camera_source": CAMERA_SOURCE,
        "uptime_s": round(time.time() - STATE["started"], 1),
        "frames_captured": STATE["frames"], "upload_backlog": pending,
        "backend_online": STATE["online"],
        "status": "ok" if STATE["frames"] > 0 else "starting",
    }


@app.on_event("startup")
def _start():
    log.info("agent_starting", {"camera_source": CAMERA_SOURCE, "fps": CAPTURE_FPS,
                                "bucket": S3_BUCKET, "endpoint": S3_ENDPOINT})
    threading.Thread(target=capture_loop, daemon=True).start()
    threading.Thread(target=upload_loop, daemon=True).start()
