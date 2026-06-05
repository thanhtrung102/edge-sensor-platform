"""Unit tests for the edge agent's correctness-critical pure logic.

Focus: the two things most likely to silently corrupt the dataset —
  1. partitioning late-arriving (buffered-during-outage) data by CAPTURE time, not upload time;
  2. bounding the local buffer so a long outage can't fill the disk.
"""
import importlib.util
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Load the agent module fresh with a throwaway buffer dir and an unreachable S3 endpoint
# (no network, no threads start on import — they only start on FastAPI's startup event).
os.environ.setdefault("BUFFER_DIR", tempfile.mkdtemp(prefix="edgebuf_"))
os.environ.setdefault("S3_ENDPOINT", "http://127.0.0.1:9")
os.environ.setdefault("DEVICE_ID", "edge-001")

_AGENT_PY = Path(__file__).resolve().parents[1] / "agent" / "agent.py"
_spec = importlib.util.spec_from_file_location("agent_under_test", _AGENT_PY)
agent = importlib.util.module_from_spec(_spec)
sys.modules["agent_under_test"] = agent
_spec.loader.exec_module(agent)


def _touch(path: Path, size: int = 10, mtime: float | None = None):
    path.write_bytes(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# --------------------------------------------------------------------------- capture time
def test_capture_time_parses_stamp():
    t = agent._capture_time(Path("frame_20260101T091500123456.jpg"))
    assert t == datetime(2026, 1, 1, 9, 15, 0, 123456, tzinfo=timezone.utc)


def test_capture_time_falls_back_to_mtime(tmp_path):
    p = _touch(tmp_path / "weirdname.jpg", mtime=1_700_000_000)
    t = agent._capture_time(p)
    assert t == datetime.fromtimestamp(1_700_000_000, timezone.utc)


def test_key_partitions_by_capture_not_upload_time(tmp_path):
    """The bug fix: a file captured at 09:00 but uploaded later must still key into the
    09:00 partition, independent of the current wall clock."""
    agent.DEVICE_ID = "edge-001"
    p = _touch(tmp_path / "frame_20260101T090000000000.jpg")
    key = agent._key_for(p)
    assert key == "edge-001/frames/2026/01/01/09/frame_20260101T090000000000.jpg"


def test_key_sensors_kind(tmp_path):
    p = _touch(tmp_path / "sensors_20260101T123000000000.json")
    assert agent._key_for(p).startswith("edge-001/sensors/2026/01/01/12/")


# --------------------------------------------------------------------------- buffer cap
def test_enforce_buffer_cap_drops_oldest_first(tmp_path):
    agent.MAX_BUFFER_BYTES = 25  # room for ~2 of our 10-byte files
    files = [
        _touch(tmp_path / f"frame_2026010{i}T000000000000.jpg", size=10, mtime=1000 + i)
        for i in range(1, 5)  # 4 files, oldest-first, 40 bytes total
    ]
    survivors = agent._enforce_buffer_cap(list(files))
    # total must end <= cap, oldest removed, newest retained
    assert sum(p.stat().st_size for p in survivors) <= agent.MAX_BUFFER_BYTES
    assert not files[0].exists() and not files[1].exists()
    assert files[-1].exists()
    assert survivors[-1] == files[-1]


def test_enforce_buffer_cap_noop_under_limit(tmp_path):
    agent.MAX_BUFFER_BYTES = 10_000
    files = [_touch(tmp_path / f"frame_2026010{i}T000000000000.jpg", size=10) for i in range(1, 4)]
    survivors = agent._enforce_buffer_cap(list(files))
    assert survivors == files
    assert all(p.exists() for p in files)
