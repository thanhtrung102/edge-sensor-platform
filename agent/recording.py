"""Recording plane: rotate camera + sensor captures into MCAP segments.

Replaces the per-frame JPEG object (the small-files problem, solved at the source rather than
compacted later) with a rolling MCAP segment carrying two synchronized channels —
``/camera/jpeg`` (Foxglove ``CompressedImage``) and ``/sensors`` (JSON) — the way a real robot
bag co-locates time-aligned streams. Each closed segment is dropped into the agent's buffer dir
so the existing store-and-forward upload loop ships it unchanged.

MCAP is the ROS 2 default bag format; these segments open directly in Foxglove.
"""
import base64
import json
import os
import time
from datetime import datetime
from pathlib import Path

from mcap.writer import Writer

# Rotate on whichever limit hits first; both env-tunable. A few-minute segment is the
# Foxglove-recommended recording granularity (closed/rotated continuously, uploaded as a unit).
SEGMENT_SECONDS = float(os.getenv("SEGMENT_SECONDS", "60"))
SEGMENT_MAX_FRAMES = int(os.getenv("SEGMENT_MAX_FRAMES", "0"))  # 0 = no frame cap

_CAMERA_SCHEMA = {
    "type": "object",
    "title": "foxglove.CompressedImage",
    "properties": {
        "timestamp": {
            "type": "object",
            "properties": {"sec": {"type": "integer"}, "nsec": {"type": "integer"}},
        },
        "frame_id": {"type": "string"},
        "data": {"type": "string", "contentEncoding": "base64"},
        "format": {"type": "string"},
    },
}
_SENSOR_SCHEMA = {
    "type": "object",
    "title": "edge.SensorReading",
    "properties": {
        "device_id": {"type": "string"},
        "site": {"type": "string"},
        "ts": {"type": "string"},
        "temperature_c": {"type": "number"},
        "vibration_g": {"type": "number"},
        "humidity_pct": {"type": "number"},
    },
}


class McapSegmentWriter:
    """Append camera frames + sensor readings to a rotating MCAP segment.

    `append()` returns the closed segment's Path on the tick that triggers a rotation, else None,
    so the caller can react (count it, log it) without owning the rotation policy.
    """

    def __init__(self, buffer_dir, device_id, on_rotate=None):
        self.buffer_dir = Path(buffer_dir)
        self.device_id = device_id
        self.on_rotate = on_rotate
        self._writer = None
        self._fh = None
        self._path = None
        self._started = 0.0
        self._frames = 0

    def _open(self, ts: datetime):
        stamp = ts.strftime("%Y%m%dT%H%M%S%f")
        # Write to a .part temp name so the upload loop never ships a half-written segment;
        # atomically renamed to the final seg_*.mcap on rotation.
        self._final = self.buffer_dir / f"seg_{stamp}.mcap"
        self._path = self.buffer_dir / f"_seg_{stamp}.mcap.part"
        self._fh = self._path.open("wb")
        self._writer = Writer(self._fh)
        self._writer.start()
        cam_schema = self._writer.register_schema(
            name="foxglove.CompressedImage", encoding="jsonschema",
            data=json.dumps(_CAMERA_SCHEMA).encode(),
        )
        sen_schema = self._writer.register_schema(
            name="edge.SensorReading", encoding="jsonschema",
            data=json.dumps(_SENSOR_SCHEMA).encode(),
        )
        self._cam_ch = self._writer.register_channel(
            topic="/camera/jpeg", message_encoding="json", schema_id=cam_schema)
        self._sen_ch = self._writer.register_channel(
            topic="/sensors", message_encoding="json", schema_id=sen_schema)
        self._started = time.time()
        self._frames = 0

    def append(self, frame_bytes: bytes, sensor_record: dict, ts: datetime):
        if self._writer is None:
            self._open(ts)
        ns = int(ts.timestamp() * 1_000_000_000)
        cam_msg = {
            "timestamp": {"sec": ns // 1_000_000_000, "nsec": ns % 1_000_000_000},
            "frame_id": self.device_id,
            "data": base64.b64encode(frame_bytes).decode(),
            "format": "jpeg",
        }
        self._writer.add_message(
            channel_id=self._cam_ch, log_time=ns, publish_time=ns, data=json.dumps(cam_msg).encode())
        self._writer.add_message(
            channel_id=self._sen_ch, log_time=ns, publish_time=ns, data=json.dumps(sensor_record).encode())
        self._frames += 1
        if self._should_rotate():
            return self.rotate()
        return None

    def _should_rotate(self) -> bool:
        if time.time() - self._started >= SEGMENT_SECONDS:
            return True
        return bool(SEGMENT_MAX_FRAMES) and self._frames >= SEGMENT_MAX_FRAMES

    def rotate(self):
        """Close the open segment and return its Path (None if nothing was open)."""
        if self._writer is None:
            return None
        self._writer.finish()
        self._fh.close()
        os.replace(self._path, self._final)  # atomic: now visible to the upload loop
        final, frames = self._final, self._frames
        self._writer = self._fh = self._path = self._final = None
        self._frames = 0
        if self.on_rotate:
            self.on_rotate(final, frames)
        return final
