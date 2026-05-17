"""Tests for Task 9: YouTube upload per-chunk cancel + timeout.

upload_video used to spin in `while response is None: status, response =
request.next_chunk()` with no cancellation check, no per-chunk retry
budget, and no transport timeout — a stalled TCP connection or a
mid-batch user cancel could pin the worker indefinitely. This suite
verifies that:

- state.upl.cancelled is now backed by a threading.Event with the same
  bool-property shim as EncodingState (so the FE's snapshot payload
  stays the same shape).
- upload_video accepts a cancel_event and checks it BETWEEN chunks, not
  just before/after the loop.
- upload_video passes num_retries=3 to next_chunk for transient-error
  recovery.
- UploadResult gained a cancelled bool so the route can distinguish
  user-abort from genuine error.
- The cancel route sets the event (no subprocess for HTTP uploads).

googleapiclient is not installed in the test container, so all tests
mock at clipcutter.youtube.get_authenticated_service (which is where the
google deps actually get imported, thanks to lazy imports).
"""

import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clipcutter.state import AppState, UploadState
from clipcutter.youtube import (
    UploadResult,
    YOUTUBE_CHUNK_RETRIES,
    YOUTUBE_HTTP_TIMEOUT_SECONDS,
    YouTubeCredentials,
    upload_video,
)


# ---------------------------------------------------------------------------
# Inject fake googleapiclient.http so upload_video's lazy
# `from googleapiclient.http import MediaFileUpload` doesn't ImportError
# in environments without google deps (CI, this test container).
# ---------------------------------------------------------------------------

if "googleapiclient" not in sys.modules:
    fake_pkg = types.ModuleType("googleapiclient")
    fake_pkg.__path__ = []  # mark as package
    sys.modules["googleapiclient"] = fake_pkg

if "googleapiclient.http" not in sys.modules:
    fake_http = types.ModuleType("googleapiclient.http")
    fake_http.MediaFileUpload = MagicMock(return_value=MagicMock())
    sys.modules["googleapiclient.http"] = fake_http
    sys.modules["googleapiclient"].http = fake_http


# ---------------------------------------------------------------------------
# State-level: cancelled bool ↔ Event compatibility (mirrors Task 8 suite).
# ---------------------------------------------------------------------------

class TestUploadStateCancelEvent:
    def test_cancelled_initial_false(self):
        s = UploadState()
        assert s.cancelled is False
        assert s.cancel_event.is_set() is False

    def test_setting_cancelled_true_sets_event(self):
        s = UploadState()
        s.cancelled = True
        assert s.cancelled is True
        assert s.cancel_event.is_set() is True

    def test_setting_cancelled_false_clears_event(self):
        s = UploadState()
        s.cancel_event.set()
        s.cancelled = False
        assert s.cancel_event.is_set() is False

    def test_reset_clears_event(self):
        s = UploadState()
        s.cancel_event.set()
        s.reset(total=2)
        assert s.cancel_event.is_set() is False
        assert s.cancelled is False

    def test_snapshot_reflects_event(self):
        s = UploadState()
        s.reset(total=1)
        assert s.snapshot()["cancelled"] is False
        s.cancel_event.set()
        assert s.snapshot()["cancelled"] is True


# ---------------------------------------------------------------------------
# UploadResult shape: cancelled field exists and defaults False.
# ---------------------------------------------------------------------------

class TestUploadResultShape:
    def test_cancelled_field_defaults_false(self):
        r = UploadResult(success=True, video_id="x", url="http://y")
        assert r.cancelled is False

    def test_cancelled_field_can_be_true(self):
        r = UploadResult(success=False, cancelled=True)
        assert r.success is False
        assert r.cancelled is True
        assert r.error is None  # cancel ≠ error


# ---------------------------------------------------------------------------
# upload_video mid-loop cancellation + num_retries plumbing.
# ---------------------------------------------------------------------------

def _fake_creds() -> YouTubeCredentials:
    return YouTubeCredentials(
        access_token="a", refresh_token="r", token_expiry=None,
        client_id="cid", client_secret="csec",
    )


def _make_fake_service_with_chunks(chunk_returns, next_chunk_side_effect=None):
    """Build a mocked service whose insert().next_chunk() returns the given
    sequence. Each entry in chunk_returns is a (status, response) tuple;
    the final entry should have response=fake_response_dict to terminate
    the loop. If next_chunk_side_effect is provided, it's used to wrap
    each call (e.g., to set a cancel event after N calls).
    """
    request = MagicMock()
    iter_returns = iter(chunk_returns)

    def next_chunk(num_retries=0):
        # Record num_retries on the mock so tests can assert on it.
        next_chunk.last_num_retries = num_retries
        next_chunk.call_count += 1
        if next_chunk_side_effect is not None:
            next_chunk_side_effect(next_chunk.call_count)
        return next(iter_returns)

    next_chunk.last_num_retries = None
    next_chunk.call_count = 0
    request.next_chunk = next_chunk

    insert_call = MagicMock()
    insert_call.return_value = request
    service = MagicMock()
    service.videos.return_value.insert = insert_call
    return service, request, next_chunk


class TestUploadVideoCancellation:
    """upload_video must check cancel_event AFTER each next_chunk so a
    user-hit cancel takes effect mid-upload, not at the next clip."""

    def test_cancel_set_before_first_chunk_returns_cancelled(self, tmp_path):
        """Sanity: event already set → loop exits without finishing."""
        fake_file = tmp_path / "clip.mp4"
        fake_file.write_bytes(b"x")

        cancel_event = threading.Event()
        cancel_event.set()

        # Chunks that would otherwise progress; we expect the loop to
        # exit on the post-chunk cancel check after the FIRST call.
        status1 = MagicMock(resumable_progress=10, total_size=100)
        service, request, next_chunk = _make_fake_service_with_chunks([
            (status1, None),                   # progress
            (None, {"id": "ABC"}),             # would terminate normally
        ])

        with patch("clipcutter.youtube.get_authenticated_service",
                   return_value=(service, _fake_creds())):
            result = upload_video(
                creds=_fake_creds(),
                file_path=fake_file,
                title="t", description="", tags=[], category_id="20",
                privacy="private",
                cancel_event=cancel_event,
            )

        assert result.success is False
        assert result.cancelled is True
        assert result.video_id is None
        # Exactly one next_chunk before the cancel check fired.
        assert next_chunk.call_count == 1

    def test_cancel_set_mid_loop_aborts_within_one_chunk(self, tmp_path):
        """MID-LOOP cancel: event is set after the 3rd next_chunk; the
        4th must not happen — the post-3rd cancel check exits the loop.
        This is the load-bearing test for the whole task."""
        fake_file = tmp_path / "clip.mp4"
        fake_file.write_bytes(b"x")

        cancel_event = threading.Event()

        # 5 chunks of progress then a final success — but the side
        # effect sets the cancel event after the 3rd call, so we should
        # exit after the 3rd next_chunk and never call the 4th/5th.
        status = MagicMock(resumable_progress=10, total_size=100)
        chunk_returns = [
            (status, None), (status, None), (status, None),
            (status, None), (None, {"id": "ABC"}),
        ]

        def side_effect(call_count):
            if call_count == 3:
                cancel_event.set()

        service, request, next_chunk = _make_fake_service_with_chunks(
            chunk_returns, next_chunk_side_effect=side_effect,
        )

        with patch("clipcutter.youtube.get_authenticated_service",
                   return_value=(service, _fake_creds())):
            result = upload_video(
                creds=_fake_creds(),
                file_path=fake_file,
                title="t", description="", tags=[], category_id="20",
                privacy="private",
                cancel_event=cancel_event,
            )

        assert result.success is False
        assert result.cancelled is True
        # Exactly 3 chunks ran — the cancel check after the 3rd one
        # broke the loop before the 4th call.
        assert next_chunk.call_count == 3

    def test_no_cancel_event_completes_normally(self, tmp_path):
        """When no cancel_event is passed, behaviour is unchanged: the
        loop runs to completion and returns success."""
        fake_file = tmp_path / "clip.mp4"
        fake_file.write_bytes(b"x")

        status = MagicMock(resumable_progress=50, total_size=100)
        service, request, next_chunk = _make_fake_service_with_chunks([
            (status, None),
            (None, {"id": "ZYX"}),
        ])

        with patch("clipcutter.youtube.get_authenticated_service",
                   return_value=(service, _fake_creds())):
            result = upload_video(
                creds=_fake_creds(),
                file_path=fake_file,
                title="t", description="", tags=[], category_id="20",
                privacy="private",
            )

        assert result.success is True
        assert result.cancelled is False
        assert result.video_id == "ZYX"
        assert result.url == "https://www.youtube.com/watch?v=ZYX"
        assert next_chunk.call_count == 2

    def test_num_retries_passed_through(self, tmp_path):
        """next_chunk must be called with num_retries=YOUTUBE_CHUNK_RETRIES
        so transient errors can recover with exponential backoff before
        surfacing to the worker loop."""
        fake_file = tmp_path / "clip.mp4"
        fake_file.write_bytes(b"x")

        service, request, next_chunk = _make_fake_service_with_chunks([
            (None, {"id": "X"}),
        ])

        with patch("clipcutter.youtube.get_authenticated_service",
                   return_value=(service, _fake_creds())):
            upload_video(
                creds=_fake_creds(),
                file_path=fake_file,
                title="t", description="", tags=[], category_id="20",
                privacy="private",
            )

        assert next_chunk.last_num_retries == YOUTUBE_CHUNK_RETRIES
        assert YOUTUBE_CHUNK_RETRIES == 3, (
            "Task 9 spec calls for num_retries=3 — bump cautiously."
        )

    def test_exception_during_chunk_reports_as_cancelled_if_event_set(self, tmp_path):
        """If the cancel event is set when an exception surfaces (e.g.,
        the socket was closed because we cancelled), the result is
        classified as cancelled, not as an error. Prevents the FE from
        showing a red "Connection reset" banner when the user is the one
        who hung up."""
        fake_file = tmp_path / "clip.mp4"
        fake_file.write_bytes(b"x")

        cancel_event = threading.Event()

        # First chunk raises mid-call; the test arranges for the event
        # to be set before next_chunk raises (simulating: route hit,
        # event set, subsequent socket op throws).
        def raising_next_chunk(num_retries=0):
            cancel_event.set()
            raise ConnectionResetError("socket closed by peer")

        request = MagicMock()
        request.next_chunk = raising_next_chunk
        service = MagicMock()
        service.videos.return_value.insert.return_value = request

        with patch("clipcutter.youtube.get_authenticated_service",
                   return_value=(service, _fake_creds())):
            result = upload_video(
                creds=_fake_creds(),
                file_path=fake_file,
                title="t", description="", tags=[], category_id="20",
                privacy="private",
                cancel_event=cancel_event,
            )

        assert result.success is False
        assert result.cancelled is True
        assert result.error is None  # not classified as a hard error

    def test_exception_without_cancel_is_error(self, tmp_path):
        """Symmetric check: a real error (no cancel) maps to error=..., not
        cancelled — so the FE still shows the failure banner."""
        fake_file = tmp_path / "clip.mp4"
        fake_file.write_bytes(b"x")

        def raising_next_chunk(num_retries=0):
            raise ConnectionResetError("real failure")

        request = MagicMock()
        request.next_chunk = raising_next_chunk
        service = MagicMock()
        service.videos.return_value.insert.return_value = request

        with patch("clipcutter.youtube.get_authenticated_service",
                   return_value=(service, _fake_creds())):
            result = upload_video(
                creds=_fake_creds(),
                file_path=fake_file,
                title="t", description="", tags=[], category_id="20",
                privacy="private",
                # no cancel_event
            )

        assert result.success is False
        assert result.cancelled is False
        assert result.error is not None
        assert "real failure" in result.error


# ---------------------------------------------------------------------------
# Route: /api/youtube/upload/cancel sets the event.
# ---------------------------------------------------------------------------

class TestCancelRoute:
    def test_cancel_route_sets_event(self, app_client):
        from clipcutter.state import AppState

        # The cancel route just sets the event; no preconditions
        # (running, registered work, etc.) and no subprocess to kill.
        resp = app_client.post("/api/youtube/upload/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelling"

        # Recover the AppState to confirm the event landed.
        # web.create_app stashes nothing, so walk closures.
        state = _recover_app_state(app_client.app)
        assert state.upl.cancel_event.is_set() is True
        assert state.upl.snapshot()["cancelled"] is True


class TestCancelRouteEndToEnd:
    """The end-to-end pipeline: POST /api/youtube/upload kicks off the
    batch worker thread; POST /api/youtube/upload/cancel sets
    state.upl.cancel_event; the worker (which passes that same event into
    upload_video) sees it and surfaces cancelled=True back to AppState.

    This is the contract the Task 9 work established: the cancel route is
    not just a flag-flip — it must actually reach a long-running upload
    via the shared event object and abort it without finishing the
    current clip."""

    def test_cancel_route_aborts_inflight_upload_via_event(
        self, output_dir, app_client, tmp_path,
    ):
        import json
        import time
        from clipcutter.config import YOUTUBE_CREDENTIALS_FILE

        # 1) Seed a real (loadable) creds file so the upload route doesn't
        #    bail with 401 before the worker thread starts.
        creds_path = output_dir / YOUTUBE_CREDENTIALS_FILE
        creds_path.parent.mkdir(parents=True, exist_ok=True)
        creds_path.write_text(json.dumps({
            "access_token": "a", "refresh_token": "r", "token_expiry": None,
            "client_id": "cid", "client_secret": "csec",
        }), encoding="utf-8")

        # 2) Seed a kept clip file the route path-validates and tries to
        #    upload. The contents don't matter — upload_video is mocked.
        stem = "ytcancel"
        filename = "clip_001.mp4"
        kept_dir = output_dir / "clips" / "kept" / stem
        kept_dir.mkdir(parents=True, exist_ok=True)
        (kept_dir / filename).write_bytes(b"stub-kept-bytes")
        # Metadata so duplicate-detection / encoded-resolve don't blow up.
        meta_dir = output_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / f"{stem}_clips.json").write_text(json.dumps({
            "source_video": f"/fake/{stem}.mp4",
            "processed_at": "2026-01-01T00:00:00",
            "clip_count": 1,
            "clips": [{
                "filename": filename,
                "source_video": f"/fake/{stem}.mp4",
                "start_time": 0.0, "end_time": 5.0, "duration": 5.0,
                "detection_reasons": ["volume_spike"], "confidence": 0.8,
                "status": "kept",
            }],
        }), encoding="utf-8")

        # 3) Capture the cancel_event passed into upload_video, and block
        #    in the fake upload until it gets set. This way we can prove
        #    the route's cancel signal reached the upload worker mid-loop.
        captured: dict = {}
        upload_seen_cancel = threading.Event()

        def fake_upload_video(
            *, creds, file_path, title, description, tags, category_id,
            privacy, progress_callback=None, cancel_event=None,
            creds_path=None,
        ):
            # Stash the event for later assertion (it must be the SAME
            # object the cancel route flips — that's the integration contract).
            captured["cancel_event"] = cancel_event
            # Block until the cancel event fires. The route's mid-loop
            # check would do the same; the fake stands in for the
            # next_chunk loop so the test doesn't have to mock googleapi.
            if cancel_event is not None and cancel_event.wait(timeout=5.0):
                upload_seen_cancel.set()
                return UploadResult(success=False, cancelled=True)
            return UploadResult(
                success=True, video_id="X", url="https://youtu.be/X",
            )

        with patch(
            "clipcutter.youtube.upload_video", side_effect=fake_upload_video,
        ):
            # 4) Kick off the batch upload.
            resp = app_client.post("/api/youtube/upload", json={
                "clips": [{
                    "video_stem": stem,
                    "filename": filename,
                    "use_encoded": False,
                    "title": "Test", "description": "", "tags": [],
                    "category_id": "20", "privacy": "private",
                }],
            })
            assert resp.status_code == 200, resp.text

            # Wait until upload_video is actually inside the loop (so the
            # cancel race window is real, not "cancel before start"). The
            # fake stores cancel_event the moment it's called.
            deadline = time.time() + 5.0
            while "cancel_event" not in captured and time.time() < deadline:
                time.sleep(0.02)
            assert "cancel_event" in captured, (
                "upload_video was never called before timeout — "
                "the route never reached the worker loop"
            )

            # 5) Fire the cancel route. This is the integration contract:
            #    the route flips state.upl.cancel_event, which must be the
            #    SAME object the worker passed to upload_video.
            cancel_resp = app_client.post("/api/youtube/upload/cancel")
            assert cancel_resp.status_code == 200

            # 6) The fake upload's .wait() returns True; it sets the
            #    upload_seen_cancel event before returning. Wait for it.
            assert upload_seen_cancel.wait(timeout=5.0), (
                "upload_video did not observe the cancel event — "
                "the route's event and the worker's event are not the same"
            )

        # The route's event MUST be the exact same Event object the
        # worker thread passed in (not a copy / not a different field).
        state = _recover_app_state(app_client.app)
        assert captured["cancel_event"] is state.upl.cancel_event

        # Wait for the worker thread to finish() so the snapshot reflects
        # the cancelled state.
        deadline = time.time() + 5.0
        while state.upl.snapshot()["running"] and time.time() < deadline:
            time.sleep(0.02)
        snap = state.upl.snapshot()
        assert snap["running"] is False
        assert snap["cancelled"] is True
        # Cancelled mid-batch: no errors recorded (cancel is not an
        # error — the unstarted work is just unstarted).
        assert snap["errors"] == []


# ---------------------------------------------------------------------------
# httplib2 timeout constant — sanity check that it's plumbed at all.
# ---------------------------------------------------------------------------

class TestHttpTimeoutConstant:
    def test_timeout_constant_is_positive_seconds(self):
        # Spec calls for ~120s. Anything 0 or negative would silently
        # disable the timeout, which would defeat the point of Task 9.
        assert YOUTUBE_HTTP_TIMEOUT_SECONDS > 0
        assert isinstance(YOUTUBE_HTTP_TIMEOUT_SECONDS, int)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recover_app_state(app) -> AppState:
    """Recover the AppState from the FastAPI app by walking route closures."""
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        closure = getattr(endpoint, "__closure__", None) or ()
        for cell in closure:
            try:
                val = cell.cell_contents
            except ValueError:
                continue
            if isinstance(val, AppState):
                return val
    raise RuntimeError("Could not recover AppState from FastAPI app")
