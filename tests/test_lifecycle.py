"""Tests for Task 15 web-lifecycle hygiene:

1. Startup cleanup runs in a background daemon thread (server is not blocked).
2. _cleanup_stale_pending sweeps .waveform.json sidecars next to stale clips.
3. ProcessingState.log_lines is bounded at PROCESS_LOG_MAXLEN.
"""

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from clipcutter.state import PROCESS_LOG_MAXLEN, ProcessingState
from clipcutter.web import _cleanup_stale_pending, create_app

from tests.conftest import save_test_metadata


# ---------------------------------------------------------------------------
# 1. Startup cleanup runs off-thread
# ---------------------------------------------------------------------------

class TestStartupCleanupOffThread:
    """The startup hook hands cleanup to a daemon thread so the route layer
    can begin serving requests immediately even on a large pending/ tree."""

    def test_cleanup_spawned_on_a_daemon_thread(self, output_dir):
        """create_app + TestClient (which fires the startup hook) should
        spawn cleanup in a daemonized Thread — not run it synchronously."""
        spawned: list[threading.Thread] = []

        real_thread = threading.Thread

        def recording_thread(*args, **kwargs):
            t = real_thread(*args, **kwargs)
            # Only record threads kicked off by the startup hook; the test
            # harness itself spins up other threads we don't care about.
            if kwargs.get("name") == "clipcutter-startup-cleanup":
                spawned.append(t)
            return t

        with patch("clipcutter.web.threading.Thread", side_effect=recording_thread):
            app = create_app(output_dir)
            with TestClient(app):  # context manager triggers startup events
                # Give the startup hook a brief beat — it just calls start()
                # synchronously, but we don't want to race the assertion.
                time.sleep(0.05)

        assert len(spawned) == 1, (
            "Startup hook should spawn exactly one cleanup thread; "
            f"got {len(spawned)}"
        )
        t = spawned[0]
        assert t.daemon is True, (
            "Cleanup thread must be daemonized so it doesn't block server shutdown"
        )

    def test_first_request_returns_while_cleanup_runs(self, output_dir):
        """Even if cleanup is artificially slow, the first request must
        return quickly — proof that cleanup isn't on the request path."""
        # Patch _cleanup_stale_pending to sleep for a noticeable interval.
        slow_started = threading.Event()
        slow_done = threading.Event()

        def slow_cleanup(_):
            slow_started.set()
            time.sleep(1.0)
            slow_done.set()

        with patch("clipcutter.web._cleanup_stale_pending", side_effect=slow_cleanup):
            app = create_app(output_dir)
            with TestClient(app) as client:
                # Wait for the cleanup thread to have actually started; if
                # cleanup were synchronous we'd be blocked inside create_app
                # / startup and never reach here within 1s.
                assert slow_started.wait(timeout=2.0), (
                    "Startup hook never invoked the cleanup function"
                )
                t0 = time.time()
                resp = client.get("/api/process/status")
                elapsed = time.time() - t0
                assert resp.status_code == 200
                # Cleanup is sleeping ~1s; request should come back well
                # under that.
                assert elapsed < 0.5, (
                    f"First request took {elapsed:.3f}s — likely blocked on cleanup"
                )
                # Cleanup is still running at this point.
                assert not slow_done.is_set(), (
                    "Cleanup finished before request — test didn't actually "
                    "prove the off-thread behaviour. Increase sleep."
                )


# ---------------------------------------------------------------------------
# 2. .waveform.json sidecar sweep
# ---------------------------------------------------------------------------

class TestWaveformSidecarSweep:
    """_cleanup_stale_pending should delete waveform sidecars next to clips
    that have been resolved (kept/discarded), then rmdir the now-empty dir."""

    def test_sidecar_for_kept_clip_is_deleted(self, output_dir):
        from clipcutter.models import ClipMetadata

        video_stem = "session_sidecar"
        # One kept clip + its sidecar that should both be swept.
        pending_dir = output_dir / "clips" / "pending" / video_stem
        pending_dir.mkdir(parents=True, exist_ok=True)
        clip_path = pending_dir / "clip_001.mp4"
        sidecar_path = pending_dir / "clip_001.mp4.waveform.json"
        clip_path.write_bytes(b"\x00" * 64)
        sidecar_path.write_text(json.dumps({"waveform": [0.1, 0.2]}))

        clip = ClipMetadata(
            filename="clip_001.mp4",
            source_video="/tmp/src.mp4",
            start_time=0.0, end_time=1.0, duration=1.0,
            detection_reasons=["volume_spike"], confidence=0.9,
            status="kept",
        )
        save_test_metadata(output_dir, video_stem, [clip], "/tmp/src.mp4")

        _cleanup_stale_pending(output_dir)

        assert not clip_path.exists(), "Kept clip's pending file should be swept"
        assert not sidecar_path.exists(), (
            "Kept clip's .waveform.json sidecar should be swept"
        )
        # Empty dir should have been rmdir'd.
        assert not pending_dir.exists(), (
            "Empty video dir should be removed once everything is swept"
        )

    def test_sidecar_for_discarded_clip_is_deleted(self, output_dir):
        from clipcutter.models import ClipMetadata

        video_stem = "session_discarded"
        pending_dir = output_dir / "clips" / "pending" / video_stem
        pending_dir.mkdir(parents=True, exist_ok=True)
        clip_path = pending_dir / "clip_002.mp4"
        sidecar_path = pending_dir / "clip_002.mp4.waveform.json"
        clip_path.write_bytes(b"\x00" * 64)
        sidecar_path.write_text("{}")

        clip = ClipMetadata(
            filename="clip_002.mp4",
            source_video="/tmp/src.mp4",
            start_time=0.0, end_time=1.0, duration=1.0,
            detection_reasons=["volume_spike"], confidence=0.7,
            status="discarded",
        )
        save_test_metadata(output_dir, video_stem, [clip], "/tmp/src.mp4")

        _cleanup_stale_pending(output_dir)

        assert not clip_path.exists()
        assert not sidecar_path.exists()
        assert not pending_dir.exists()

    def test_sidecar_for_pending_clip_is_preserved(self, output_dir):
        """Clips still awaiting review must keep BOTH their file and sidecar
        — the sweep should only fire on resolved clips."""
        from clipcutter.models import ClipMetadata

        video_stem = "session_keep"
        pending_dir = output_dir / "clips" / "pending" / video_stem
        pending_dir.mkdir(parents=True, exist_ok=True)
        clip_path = pending_dir / "clip_003.mp4"
        sidecar_path = pending_dir / "clip_003.mp4.waveform.json"
        clip_path.write_bytes(b"\x00" * 64)
        sidecar_path.write_text(json.dumps({"waveform": [0.5]}))

        clip = ClipMetadata(
            filename="clip_003.mp4",
            source_video="/tmp/src.mp4",
            start_time=0.0, end_time=1.0, duration=1.0,
            detection_reasons=["volume_spike"], confidence=0.8,
            status="pending",
        )
        save_test_metadata(output_dir, video_stem, [clip], "/tmp/src.mp4")

        _cleanup_stale_pending(output_dir)

        assert clip_path.exists(), "Pending clip must not be deleted"
        assert sidecar_path.exists(), "Pending clip's sidecar must be preserved"
        assert pending_dir.exists(), "Dir with pending content must remain"

    def test_mixed_clips_keep_pending_drop_resolved(self, output_dir):
        """A video dir with a mix of pending and resolved clips: sweep
        removes resolved files+sidecars, leaves pending intact, leaves dir."""
        from clipcutter.models import ClipMetadata

        video_stem = "session_mixed"
        pending_dir = output_dir / "clips" / "pending" / video_stem
        pending_dir.mkdir(parents=True, exist_ok=True)

        # Clip A — kept (should be swept)
        a_clip = pending_dir / "clip_a.mp4"
        a_side = pending_dir / "clip_a.mp4.waveform.json"
        a_clip.write_bytes(b"\x00")
        a_side.write_text("{}")
        # Clip B — pending (should remain)
        b_clip = pending_dir / "clip_b.mp4"
        b_side = pending_dir / "clip_b.mp4.waveform.json"
        b_clip.write_bytes(b"\x00")
        b_side.write_text("{}")

        clips = [
            ClipMetadata(
                filename="clip_a.mp4", source_video="/x", start_time=0.0,
                end_time=1.0, duration=1.0,
                detection_reasons=["volume_spike"], confidence=0.9,
                status="kept",
            ),
            ClipMetadata(
                filename="clip_b.mp4", source_video="/x", start_time=0.0,
                end_time=1.0, duration=1.0,
                detection_reasons=["volume_spike"], confidence=0.9,
                status="pending",
            ),
        ]
        save_test_metadata(output_dir, video_stem, clips, "/x")

        _cleanup_stale_pending(output_dir)

        assert not a_clip.exists() and not a_side.exists(), (
            "Kept clip + sidecar should be swept"
        )
        assert b_clip.exists() and b_side.exists(), (
            "Pending clip + sidecar should remain"
        )
        assert pending_dir.exists(), "Dir should remain because pending clip is in it"


# ---------------------------------------------------------------------------
# 3. log_lines bounded
# ---------------------------------------------------------------------------

class TestProcessLogBounded:
    """ProcessingState.log_lines is a deque(maxlen=PROCESS_LOG_MAXLEN), so a
    long run produces a bounded payload on every /api/process/status poll."""

    def test_log_lines_caps_at_maxlen(self):
        state = ProcessingState()
        for i in range(PROCESS_LOG_MAXLEN + 100):
            state.add_line(f"line {i}")
        snap = state.snapshot()
        assert len(snap["log"]) == PROCESS_LOG_MAXLEN, (
            f"log should cap at {PROCESS_LOG_MAXLEN}; got {len(snap['log'])}"
        )
        # Oldest lines should have been evicted; the tail should be the
        # most-recent additions.
        assert snap["log"][-1] == f"line {PROCESS_LOG_MAXLEN + 99}"
        assert snap["log"][0] == "line 100", (
            "Eviction should drop the oldest lines first"
        )

    def test_log_lines_default_maxlen_is_500(self):
        """Lock in the documented cap so a future bump is intentional."""
        assert PROCESS_LOG_MAXLEN == 500

    def test_reset_starts_fresh_bounded_deque(self):
        state = ProcessingState()
        for i in range(50):
            state.add_line(f"first {i}")
        state.reset()
        # After reset, the buffer is empty but still bounded.
        snap = state.snapshot()
        assert snap["log"] == []
        for i in range(PROCESS_LOG_MAXLEN + 10):
            state.add_line(f"after {i}")
        snap = state.snapshot()
        assert len(snap["log"]) == PROCESS_LOG_MAXLEN
        assert snap["log"][0] == "after 10"

    def test_snapshot_is_a_shallow_copy(self):
        """snapshot() must hand out a list independent of the underlying
        deque so callers can't mutate state by appending to it."""
        state = ProcessingState()
        state.add_line("a")
        snap1 = state.snapshot()
        snap1["log"].append("injected")
        snap2 = state.snapshot()
        assert snap2["log"] == ["a"], (
            "Mutating snapshot's log must not affect future snapshots"
        )
