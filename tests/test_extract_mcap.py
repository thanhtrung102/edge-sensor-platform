"""The pipeline must recover a per-frame index from MCAP recording segments (the recording plane).

Builds a real segment with the agent's writer, then runs the extract's MCAP reader over its bytes —
the same round-trip extract.py performs against object storage, minus boto3/duckdb.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "agent"))      # recording.McapSegmentWriter
sys.path.insert(0, str(_ROOT / "pipeline"))   # mcap_index.frames_from_segment

import recording  # noqa: E402
from mcap_index import frames_from_segment  # noqa: E402


def test_frames_from_segment_recovers_capture_time(tmp_path):
    recording.SEGMENT_SECONDS = 9999
    recording.SEGMENT_MAX_FRAMES = 4
    sealed = []
    w = recording.McapSegmentWriter(tmp_path, "edge-001", on_rotate=lambda p, n: sealed.append(p))
    base = datetime(2026, 6, 5, 15, 30, 0, tzinfo=timezone.utc)
    for i in range(4):
        ts = base.replace(second=i * 10)
        w.append(b"\xff\xd8frame" + bytes([i]), {"device_id": "edge-001", "ts": ts.isoformat()}, ts)

    key = "edge-001/recordings/2026/06/05/15/" + sealed[0].name
    rows = list(frames_from_segment(sealed[0].read_bytes(), key))

    assert len(rows) == 4                                  # one row per camera frame
    assert all(r["segment_key"] == key for r in rows)
    assert all(r["size_bytes"] > 0 for r in rows)
    # capture time recovered from the message log-time (not a filename), partition-correct
    assert rows[0]["captured_at"].startswith("2026-06-05T15:30:00")
    assert rows[3]["captured_at"].startswith("2026-06-05T15:30:30")
