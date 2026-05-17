"""Tests for Task 8: Popen-based subprocess cancellation.

The encode and keep workers used to only check a boolean cancel flag
between iterations of a batch loop, so an in-flight ffmpeg invocation
had to finish naturally before cancellation could take effect. This
suite verifies that:

- state.enc.cancelled is now backed by a threading.Event with snapshot
  parity (the FE expects {"cancelled": bool} on the status payload).
- state.keep exposes per-task cancel/popen helpers.
- the encode + keep cancel routes terminate the registered Popen.
- a real long-running subprocess is actually killed by the cancel
  route, not merely flagged.

ffmpeg is not available in the test container, so most tests mock
subprocess.Popen. The real-subprocess test uses /usr/bin/sleep as a
stand-in for a long-running ffmpeg call.
"""

import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clipcutter.state import AppState, EncodingState, KeepState


# ---------------------------------------------------------------------------
# State-level: cancelled bool ↔ Event compatibility
# ---------------------------------------------------------------------------

class TestEncodingStateCancelEvent:
    def test_cancelled_initial_false(self):
        s = EncodingState()
        assert s.cancelled is False
        assert s.cancel_event.is_set() is False

    def test_setting_cancelled_true_sets_event(self):
        s = EncodingState()
        s.cancelled = True
        assert s.cancelled is True
        assert s.cancel_event.is_set() is True

    def test_setting_cancelled_false_clears_event(self):
        s = EncodingState()
        s.cancel_event.set()
        s.cancelled = False
        assert s.cancel_event.is_set() is False

    def test_reset_clears_event_and_popen(self):
        s = EncodingState()
        s.cancel_event.set()
        s.popen = MagicMock()
        s.reset(total=2)
        assert s.cancel_event.is_set() is False
        assert s.popen is None

    def test_snapshot_reflects_event(self):
        s = EncodingState()
        s.reset(total=1)
        snap = s.snapshot()
        assert snap["cancelled"] is False
        s.cancel_event.set()
        snap = s.snapshot()
        assert snap["cancelled"] is True

    def test_set_get_popen_roundtrip(self):
        s = EncodingState()
        fake = MagicMock(spec=subprocess.Popen)
        s.set_popen(fake)
        assert s.get_popen() is fake
        s.set_popen(None)
        assert s.get_popen() is None


class TestKeepStateCancelHelpers:
    def test_cancel_unknown_task_returns_false(self):
        ks = KeepState()
        assert ks.cancel("no-such-task") is False

    def test_cancel_sets_event_for_existing_task(self):
        ks = KeepState()
        tid = ks.start("stem", "clip.mp4")
        evt = ks.get_cancel_event(tid)
        assert evt is not None
        assert evt.is_set() is False
        assert ks.cancel(tid) is True
        assert evt.is_set() is True

    def test_popen_set_get_per_task(self):
        ks = KeepState()
        tid = ks.start("stem", "clip.mp4")
        assert ks.get_popen(tid) is None
        fake = MagicMock(spec=subprocess.Popen)
        ks.set_popen(tid, fake)
        assert ks.get_popen(tid) is fake
        ks.set_popen(tid, None)
        assert ks.get_popen(tid) is None

    def test_finish_with_cancelled_flag_sets_status(self):
        ks = KeepState()
        tid = ks.start("stem", "clip.mp4")
        ks.finish(tid, cancelled=True)
        snap = ks.snapshot()
        task = snap["tasks"][0]
        assert task["status"] == "cancelled"
        assert task["error"] is None


# ---------------------------------------------------------------------------
# Encode cancel route — mocked Popen
# ---------------------------------------------------------------------------

class TestEncodeCancelRoute:
    """Cancel route should terminate the registered Popen and set the event."""

    def test_cancel_with_no_popen_just_sets_event(self, app_client):
        """Idempotent when nothing is running — should not blow up."""
        # app_client fixture already gave us a fresh state.
        resp = app_client.post("/api/encode/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelling"

    def test_cancel_calls_terminate_on_registered_popen(self, output_dir):
        """When state.enc.popen is set, cancel route should terminate it."""
        from fastapi.testclient import TestClient
        from clipcutter.web import create_app
        from clipcutter.state import AppState

        app = create_app(output_dir)
        # The app's state is stashed on app.state for inspection (set in
        # web.py). We reach in via the create_app's app.extra_state if
        # available, or via app.state.
        client = TestClient(app)

        # Reach the AppState the routes are using.
        enc_state = _get_app_state(app).enc

        fake_proc = MagicMock(spec=subprocess.Popen)
        # Default: wait succeeds (process exited cleanly after terminate).
        fake_proc.wait.return_value = 0
        enc_state.set_popen(fake_proc)

        resp = client.post("/api/encode/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelling"

        # Event set + Popen.terminate called.
        assert enc_state.cancel_event.is_set()
        fake_proc.terminate.assert_called_once()
        # wait was attempted (with timeout=5)
        fake_proc.wait.assert_called_once()
        wait_kwargs = fake_proc.wait.call_args.kwargs
        assert wait_kwargs.get("timeout") == 5

    def test_cancel_force_kills_if_terminate_hangs(self, output_dir):
        """If .wait() times out, the cancel route should escalate to .kill()."""
        from fastapi.testclient import TestClient
        from clipcutter.web import create_app

        app = create_app(output_dir)
        client = TestClient(app)
        enc_state = _get_app_state(app).enc

        fake_proc = MagicMock(spec=subprocess.Popen)
        fake_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5)
        enc_state.set_popen(fake_proc)

        resp = client.post("/api/encode/cancel")
        assert resp.status_code == 200

        fake_proc.terminate.assert_called_once()
        fake_proc.kill.assert_called_once()

    def test_cancel_swallows_already_exited_terminate(self, output_dir):
        """If terminate() raises (process already gone), cancel should not 500."""
        from fastapi.testclient import TestClient
        from clipcutter.web import create_app

        app = create_app(output_dir)
        client = TestClient(app)
        enc_state = _get_app_state(app).enc

        fake_proc = MagicMock(spec=subprocess.Popen)
        fake_proc.terminate.side_effect = ProcessLookupError("already gone")
        fake_proc.wait.return_value = 0
        enc_state.set_popen(fake_proc)

        resp = client.post("/api/encode/cancel")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Keep cancel route — mocked Popen
# ---------------------------------------------------------------------------

class TestKeepCancelRoute:
    def test_cancel_unknown_task_returns_404(self, app_client):
        resp = app_client.post("/api/clips/keep/no-such-task/cancel")
        assert resp.status_code == 404

    def test_cancel_sets_event_and_terminates_popen(self, output_dir):
        from fastapi.testclient import TestClient
        from clipcutter.web import create_app

        app = create_app(output_dir)
        client = TestClient(app)
        keep_state = _get_app_state(app).keep

        tid = keep_state.start("stem", "clip.mp4")
        fake_proc = MagicMock(spec=subprocess.Popen)
        fake_proc.wait.return_value = 0
        keep_state.set_popen(tid, fake_proc)

        resp = client.post(f"/api/clips/keep/{tid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelling"

        evt = keep_state.get_cancel_event(tid)
        assert evt.is_set()
        fake_proc.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# Real-subprocess cancellation via /usr/bin/sleep
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not Path("/usr/bin/sleep").exists() and shutil.which("sleep") is None,
                    reason="sleep binary not available")
class TestRealSubprocessTermination:
    """End-to-end: a real long-running Popen is registered, cancel route
    is hit, and the process actually dies (returncode != 0, returns
    quickly). This catches regressions where .terminate() is wired to
    the wrong field or never reaches the OS."""

    def test_encode_cancel_terminates_real_subprocess(self, output_dir):
        from fastapi.testclient import TestClient
        from clipcutter.web import create_app

        app = create_app(output_dir)
        client = TestClient(app)
        enc_state = _get_app_state(app).enc

        # Start a long sleep as a stand-in for an in-flight ffmpeg.
        proc = subprocess.Popen(
            ["sleep", "30"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        enc_state.set_popen(proc)

        t0 = time.monotonic()
        resp = client.post("/api/encode/cancel")
        assert resp.status_code == 200

        # The cancel route waits up to 5s for the process to die. SIGTERM
        # to `sleep` should land within milliseconds, so we should
        # observe an exit well before the 5s ceiling.
        try:
            rc = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("Cancel did not terminate the registered subprocess")

        elapsed = time.monotonic() - t0
        assert rc != 0, f"sleep exited 0 (was not terminated). rc={rc}"
        assert elapsed < 5, f"Cancel took {elapsed:.2f}s — too slow"
        assert enc_state.cancel_event.is_set()


# ---------------------------------------------------------------------------
# Compile cancel route was REMOVED in Task 10 — make sure it stays gone.
# ---------------------------------------------------------------------------

class TestCompileCancelRouteRemoved:
    """Task 10 removed POST /api/compilation/cancel because the compile
    worker has no cancellation seams — the work is a single ffmpeg
    invocation kicked off in a daemon thread and there's nowhere for the
    cancel signal to land between syscalls. The route used to claim
    "cancelling" but never actually stopped anything; deleting it is the
    honest fix. This test guards against a well-meaning re-add."""

    def test_compilation_cancel_route_gone(self, app_client):
        # 404 if no route matches at all; 405 if the only registered
        # route at this path is DELETE (the per-id delete route uses
        # /api/compilation/{compilation_id}, which matches "cancel" as
        # an id and refuses POST). Either is fine — what we're proving
        # is that NO cancel handler exists.
        resp = app_client.post("/api/compilation/cancel")
        assert resp.status_code in (404, 405)


# ---------------------------------------------------------------------------
# 409 "already in progress" branches
# ---------------------------------------------------------------------------
#
# Each of POST /api/process, /api/encode, /api/compilation must refuse a
# second start while the first is still in flight. We flip state.X.running
# manually instead of starting a real worker thread — the route only checks
# the boolean, so the test is deterministic and doesn't depend on ffmpeg.

class TestProcessAlreadyRunning409:
    def test_process_409_when_already_running(self, output_dir, app_client):
        state = _get_app_state(app_client.app)
        # Flag the processing state as running without actually starting
        # a background thread; the route should refuse to start a second.
        state.proc.running = True
        try:
            resp = app_client.post("/api/process", json={
                # folder doesn't have to exist — the 409 fires before any
                # filesystem check.
                "folder": str(output_dir),
                "sensitivity": 1.0,
            })
        finally:
            state.proc.running = False
        assert resp.status_code == 409
        assert "progress" in resp.json()["detail"].lower()


class TestEncodeAlreadyRunning409:
    def test_encode_409_when_already_running(self, output_dir, app_client):
        state = _get_app_state(app_client.app)
        state.enc.running = True
        try:
            resp = app_client.post("/api/encode", json={
                "clips": [{"video_stem": "any", "filename": "any.mp4"}],
                "preset": "original",
            })
        finally:
            state.enc.running = False
        assert resp.status_code == 409
        assert "progress" in resp.json()["detail"].lower()


class TestCompilationAlreadyRunning409:
    def test_compilation_409_when_already_running(self, output_dir, app_client):
        state = _get_app_state(app_client.app)
        state.comp.running = True
        try:
            resp = app_client.post("/api/compilation", json={
                "clips": [
                    {"video_stem": "a", "filename": "1.mp4"},
                    {"video_stem": "a", "filename": "2.mp4"},
                ],
                "transition": "cut",
            })
        finally:
            state.comp.running = False
        assert resp.status_code == 409
        assert "progress" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_app_state(app) -> AppState:
    """Recover the AppState wired into this FastAPI app's routes.

    web.create_app stashes it as a module-private; the route closures
    capture it. The simplest unambiguous accessor is the dependency-free
    one we put in web.py — but to keep this test self-contained we
    walk the routes for any closure-captured AppState reference.
    """
    # Cheapest path: web.create_app sets app.state.app_state.
    state = getattr(app.state, "app_state", None)
    if isinstance(state, AppState):
        return state
    # Fallback: scan registered routes for a closure capturing AppState.
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        closure = getattr(endpoint, "__closure__", None) or ()
        for cell in closure:
            val = cell.cell_contents
            if isinstance(val, AppState):
                return val
    raise RuntimeError("Could not recover AppState from FastAPI app")
