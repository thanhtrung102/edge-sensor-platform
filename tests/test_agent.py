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

_AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(_AGENT_DIR))  # so agent.py can `import recording`
_AGENT_PY = _AGENT_DIR / "agent.py"
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


# --------------------------------------------------------------------------- recording plane
def test_capture_time_parses_segment_stamp():
    """MCAP segments are named seg_<stamp>; they must partition by capture time like frames do."""
    t = agent._capture_time(Path("seg_20260605T154732374989.mcap"))
    assert t == datetime(2026, 6, 5, 15, 47, 32, 374989, tzinfo=timezone.utc)


def test_key_routes_mcap_to_recordings(tmp_path):
    p = _touch(tmp_path / "seg_20260101T090000000000.mcap")
    assert agent._key_for(p).startswith("edge-001/recordings/2026/01/01/09/")


def test_key_routes_three_planes(tmp_path):
    assert "/recordings/" in agent._key_for(Path("seg_20260101T090000000000.mcap"))
    assert "/sensors/" in agent._key_for(Path("sensors_20260101T090000000000.json"))
    assert "/frames/" in agent._key_for(Path("frame_20260101T090000000000.jpg"))


def test_mcap_segment_writer_rotates_and_carries_both_channels(tmp_path):
    """The recording plane must seal a segment with both camera + sensor channels, and the
    sealed file (seg_*.mcap, not the *.part temp) must be what's left for the upload loop."""
    from collections import Counter

    from mcap.reader import make_reader

    from recording import McapSegmentWriter

    import recording
    recording.SEGMENT_SECONDS = 9999       # rotate strictly on frame count for the test
    recording.SEGMENT_MAX_FRAMES = 3

    sealed = []
    w = McapSegmentWriter(tmp_path, "edge-001", on_rotate=lambda p, n: sealed.append((p, n)))
    base = datetime(2026, 6, 5, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        ts = base.replace(second=i)
        w.append(b"\xff\xd8jpeg" + bytes([i]), {"device_id": "edge-001", "ts": ts.isoformat()}, ts)

    assert len(sealed) == 1 and sealed[0][1] == 3            # one segment, three frames
    seg_path = sealed[0][0]
    assert seg_path.suffix == ".mcap" and seg_path.exists()
    assert not list(tmp_path.glob("*.part"))                 # temp renamed away

    with seg_path.open("rb") as fh:
        ch = Counter(c.topic for _s, c, _m in make_reader(fh).iter_messages())
    assert ch["/camera/jpeg"] == 3 and ch["/sensors"] == 3
