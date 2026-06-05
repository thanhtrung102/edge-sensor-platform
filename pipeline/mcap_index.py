"""Recover a frame index from an MCAP recording segment.

The recording plane stores camera frames inside rotated MCAP segments (see agent/recording.py),
not as one object per frame. To build the same per-frame `frame_index` the marts expect, the
extract walks each segment's ``/camera/jpeg`` channel and emits one row per message — taking the
capture time from the message log-time (not a filename), which keeps partition/time correctness.

Kept dependency-light (only ``mcap``) so it's unit-testable and CI-runnable without DuckDB/boto3.
"""
import io
from collections.abc import Iterator
from datetime import datetime, timezone

from mcap.reader import make_reader

CAMERA_TOPIC = "/camera/jpeg"


def frames_from_segment(body: bytes, segment_key: str) -> Iterator[dict]:
    """Yield {segment_key, captured_at (ISO UTC), size_bytes} for each camera frame in the segment."""
    reader = make_reader(io.BytesIO(body))
    for _schema, _channel, msg in reader.iter_messages(topics=[CAMERA_TOPIC]):
        captured_at = datetime.fromtimestamp(msg.log_time / 1e9, timezone.utc)
        yield {
            "segment_key": segment_key,
            "captured_at": captured_at.isoformat(),
            "size_bytes": len(msg.data),
        }
