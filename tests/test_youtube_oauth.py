"""Tests for Task 12: OAuth correctness fixes.

Five sub-changes under test:

1. get_auth_url generates a single-use random `state` parameter and
   /oauth/callback rejects mismatched / missing state with 400.
2. load_credentials returns None when refresh_token is empty (was
   returning a stub that fooled authenticated-only routes into thinking
   the user was signed in).
3. youtube_upload worker re-persists creds when the access token gets
   refreshed mid-batch, preventing invalid_grant on subsequent clips.
4. youtube_status: RefreshError → {authenticated: False}; HttpError /
   other exceptions surface with class + message instead of always
   reporting "expired credentials".
5. upload_video classifies HttpError 4xx as permanent, 5xx as transient,
   and exposes that on UploadResult.permanent.

googleapiclient is not installed in the test container, so we mock at
clipcutter.youtube.get_authenticated_service / .load_credentials /
.save_credentials and fake the google exception classes via
sys.modules injection (same pattern as test_youtube_cancel.py).
"""

import json
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clipcutter.state import AppState, UploadState
from clipcutter.youtube import (
    UploadResult,
    YouTubeCredentials,
    get_auth_url,
    load_credentials,
    save_credentials,
    upload_video,
)


# ---------------------------------------------------------------------------
# Inject fake google/googleapiclient modules so lazy imports inside the
# code under test don't ImportError, AND so we can hand the routes/youtube
# RefreshError / HttpError classes to isinstance-check against.
# ---------------------------------------------------------------------------

if "googleapiclient" not in sys.modules:
    fake_pkg = types.ModuleType("googleapiclient")
    fake_pkg.__path__ = []
    sys.modules["googleapiclient"] = fake_pkg

if "googleapiclient.http" not in sys.modules:
    fake_http = types.ModuleType("googleapiclient.http")
    fake_http.MediaFileUpload = MagicMock(return_value=MagicMock())
    sys.modules["googleapiclient.http"] = fake_http
    sys.modules["googleapiclient"].http = fake_http

# Fake googleapiclient.errors.HttpError — real one wraps an httplib2
# response with .status. We mimic the shape just enough for the
# isinstance + status-inspection code to work.
if "googleapiclient.errors" not in sys.modules:
    fake_errors = types.ModuleType("googleapiclient.errors")

    class _FakeHttpError(Exception):
        def __init__(self, resp, content=b"", uri=None):
            super().__init__(f"HttpError {getattr(resp, 'status', '?')}: {content!r}")
            self.resp = resp
            self.content = content
            self.uri = uri

    fake_errors.HttpError = _FakeHttpError
    sys.modules["googleapiclient.errors"] = fake_errors
    sys.modules["googleapiclient"].errors = fake_errors

# Fake google.auth.exceptions.RefreshError so the status route can
# isinstance-check against it.
if "google" not in sys.modules:
    fake_google = types.ModuleType("google")
    fake_google.__path__ = []
    sys.modules["google"] = fake_google

if "google.auth" not in sys.modules:
    fake_auth = types.ModuleType("google.auth")
    fake_auth.__path__ = []
    sys.modules["google.auth"] = fake_auth
    sys.modules["google"].auth = fake_auth

if "google.auth.exceptions" not in sys.modules:
    fake_exc = types.ModuleType("google.auth.exceptions")

    class _FakeRefreshError(Exception):
        pass

    fake_exc.RefreshError = _FakeRefreshError
    sys.modules["google.auth.exceptions"] = fake_exc
    sys.modules["google.auth"].exceptions = fake_exc


# Convenience handles for the fake classes (after sys.modules injection).
from googleapiclient.errors import HttpError as FakeHttpError  # noqa: E402
from google.auth.exceptions import RefreshError as FakeRefreshError  # noqa: E402


def _fake_creds(access="a", refresh="r"):
    return YouTubeCredentials(
        access_token=access, refresh_token=refresh, token_expiry=None,
        client_id="cid", client_secret="csec",
    )


def _http_response(status: int):
    """Minimal stand-in for an httplib2.Response (.status attribute)."""
    r = MagicMock()
    r.status = status
    return r


# ===========================================================================
# Sub-change 1 + 4: OAuth state generation, callback validation, error param
# ===========================================================================

class TestGetAuthUrlState:
    """get_auth_url must generate a fresh random state and embed it."""

    def test_state_is_present_in_url(self):
        url, state = get_auth_url("client-id", "http://localhost:8000/oauth/callback")
        assert "state=" in url
        assert state in url

    def test_state_is_random_per_call(self):
        _, state1 = get_auth_url("c", "http://x/cb")
        _, state2 = get_auth_url("c", "http://x/cb")
        assert state1 != state2

    def test_state_is_url_safe_and_long_enough(self):
        # secrets.token_urlsafe(32) yields ~43 base64-url chars; anything
        # shorter than 32 is too guessable, anything with `=` or `+`
        # would break round-tripping through Google's redirect.
        _, state = get_auth_url("c", "http://x/cb")
        assert len(state) >= 32
        assert "=" not in state
        assert "+" not in state
        assert "/" not in state


class TestAppStateOAuthSlot:
    """AppState owns the pending state; consume_* clears it (single-use)."""

    def test_initial_state_is_none(self, tmp_path):
        s = AppState(tmp_path)
        assert s.consume_youtube_oauth_state() is None

    def test_set_then_consume_returns_value(self, tmp_path):
        s = AppState(tmp_path)
        s.set_youtube_oauth_state("tok-123")
        assert s.consume_youtube_oauth_state() == "tok-123"

    def test_consume_clears_after_first_call(self, tmp_path):
        """Single-use semantics: second consume returns None even after set."""
        s = AppState(tmp_path)
        s.set_youtube_oauth_state("tok-123")
        s.consume_youtube_oauth_state()
        assert s.consume_youtube_oauth_state() is None

    def test_set_overwrites_prior_pending(self, tmp_path):
        """If the user starts auth twice without completing the first, the
        second flow's state replaces the first — only one in-flight."""
        s = AppState(tmp_path)
        s.set_youtube_oauth_state("old")
        s.set_youtube_oauth_state("new")
        assert s.consume_youtube_oauth_state() == "new"


class TestOAuthCallbackStateValidation:
    """The OAuth callback rejects requests with bad state, missing state,
    or upstream ?error= without ever calling exchange_code."""

    def _start_auth(self, client):
        """Helper: kick off /auth/start to seed AppState with a known state."""
        resp = client.post(
            "/api/youtube/auth/start",
            json={"client_id": "fake-cid", "client_secret": "fake-csec"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Pull the state out of the returned auth_url so the test mirrors
        # what a real browser would echo back to /oauth/callback.
        url = body["auth_url"]
        # quick parse: state is one of the &state=... params
        from urllib.parse import urlparse, parse_qs
        return parse_qs(urlparse(url).query)["state"][0]

    def test_missing_state_returns_400(self, app_client):
        # /auth/start NOT called — so AppState has no expected_state. A
        # callback without state must 400.
        resp = app_client.get("/oauth/callback?code=abc")
        assert resp.status_code == 400
        assert "state" in resp.text.lower()

    def test_mismatched_state_returns_400(self, app_client):
        # /auth/start seeds the real state; we hit the callback with a
        # wrong one. Must reject without ever calling exchange_code.
        _ = self._start_auth(app_client)
        with patch("clipcutter.youtube.exchange_code") as mock_exchange:
            resp = app_client.get(
                "/oauth/callback?code=abc&state=NOT-THE-RIGHT-STATE"
            )
        assert resp.status_code == 400
        assert "state" in resp.text.lower()
        mock_exchange.assert_not_called()

    def test_matching_state_proceeds_to_exchange(self, app_client):
        """Happy path: matching state → exchange_code called and creds saved."""
        real_state = self._start_auth(app_client)

        good_creds = _fake_creds()
        with patch("clipcutter.youtube.exchange_code",
                   return_value=good_creds) as mock_exchange:
            resp = app_client.get(
                f"/oauth/callback?code=THE-CODE&state={real_state}"
            )
        assert resp.status_code == 200
        mock_exchange.assert_called_once()
        # exchange_code was given the code from the redirect.
        kwargs = mock_exchange.call_args.kwargs
        assert kwargs["code"] == "THE-CODE"

    def test_state_is_single_use(self, app_client):
        """A second callback with the SAME state must 400 — the state was
        consumed by the first call and cleared. Prevents replay."""
        real_state = self._start_auth(app_client)

        with patch("clipcutter.youtube.exchange_code", return_value=_fake_creds()):
            resp1 = app_client.get(
                f"/oauth/callback?code=A&state={real_state}"
            )
        assert resp1.status_code == 200

        # Same state, second callback — should be rejected now.
        with patch("clipcutter.youtube.exchange_code") as mock_exchange:
            resp2 = app_client.get(
                f"/oauth/callback?code=B&state={real_state}"
            )
        assert resp2.status_code == 400
        mock_exchange.assert_not_called()

    def test_upstream_error_param_returns_400(self, app_client):
        """Google forwards user-cancel as ?error=access_denied&state=...;
        we surface it as 400 with the upstream code, never call exchange."""
        real_state = self._start_auth(app_client)

        with patch("clipcutter.youtube.exchange_code") as mock_exchange:
            resp = app_client.get(
                f"/oauth/callback?error=access_denied&state={real_state}"
            )
        assert resp.status_code == 400
        assert "access_denied" in resp.text
        mock_exchange.assert_not_called()

    def test_auth_start_no_longer_writes_stub_creds_file(self, app_client, tmp_path):
        """The old behavior — write a partial creds file with empty tokens
        — is gone. Calling /auth/start should NOT create the creds file."""
        from clipcutter.config import YOUTUBE_CREDENTIALS_FILE

        # AppState recovery to find the creds path used by this app.
        state = _recover_app_state(app_client.app)
        creds_path = state.output_dir / YOUTUBE_CREDENTIALS_FILE

        # Pre-condition: no file.
        assert not creds_path.exists()

        # Kick off auth.
        resp = app_client.post(
            "/api/youtube/auth/start",
            json={"client_id": "cid", "client_secret": "csec"},
        )
        assert resp.status_code == 200

        # File still absent — no stub was written.
        assert not creds_path.exists()


# ===========================================================================
# Sub-change 2: load_credentials returns None when refresh_token is empty
# ===========================================================================

class TestLoadCredentialsRejectsPartial:
    """A creds JSON with empty refresh_token can't be used to refresh
    an expired access token, so load_credentials must surface as None
    instead of returning a stub that fools authenticated-only routes."""

    def _write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_empty_refresh_token_returns_none(self, tmp_path):
        p = tmp_path / ".creds.json"
        self._write(p, {
            "access_token": "",
            "refresh_token": "",
            "token_expiry": None,
            "client_id": "cid",
            "client_secret": "csec",
        })
        assert load_credentials(p) is None

    def test_missing_refresh_token_returns_none(self, tmp_path):
        """Even if access_token is populated, no refresh_token = unusable
        for long-lived sessions. Treat the same as a missing file."""
        p = tmp_path / ".creds.json"
        self._write(p, {
            "access_token": "live-token",
            # refresh_token deliberately absent
            "token_expiry": None,
            "client_id": "cid",
            "client_secret": "csec",
        })
        assert load_credentials(p) is None

    def test_present_refresh_token_returns_creds(self, tmp_path):
        """Sanity: a real refresh_token round-trips."""
        p = tmp_path / ".creds.json"
        self._write(p, {
            "access_token": "live-token",
            "refresh_token": "refresh-me",
            "token_expiry": None,
            "client_id": "cid",
            "client_secret": "csec",
        })
        creds = load_credentials(p)
        assert creds is not None
        assert creds.refresh_token == "refresh-me"

    def test_missing_file_still_returns_none(self, tmp_path):
        """Regression: behavior on missing file is unchanged."""
        assert load_credentials(tmp_path / "nope.json") is None


# ===========================================================================
# Sub-change 3: refreshed tokens persist mid-batch
# ===========================================================================

class TestUploadVideoPersistsRefreshedToken:
    """If google's library refreshes the access token during next_chunk
    (it does so automatically on 401), upload_video must persist the
    new token before returning so the next clip in the batch can use it."""

    def test_token_change_during_upload_is_persisted(self, tmp_path):
        """The fake service exposes _http.credentials.token; we mutate it
        between chunks to simulate Google's auto-refresh, then assert
        the on-disk creds file shows the new token."""
        fake_file = tmp_path / "clip.mp4"
        fake_file.write_bytes(b"x")
        creds_path = tmp_path / ".creds.json"

        # Seed disk with the ORIGINAL access token so we can compare.
        save_credentials(_fake_creds(access="OLD"), creds_path)

        # Build a fake service whose _http.credentials.token starts as
        # OLD, then flips to NEW after the first chunk.
        fake_google_creds = MagicMock()
        fake_google_creds.token = "OLD"
        fake_google_creds.refresh_token = "r"
        fake_http = MagicMock()
        fake_http.credentials = fake_google_creds

        request = MagicMock()
        call = {"n": 0}

        def next_chunk(num_retries=0):
            call["n"] += 1
            if call["n"] == 1:
                # Simulate Google's 401 → refresh → retry: token mutates.
                fake_google_creds.token = "NEW"
                status = MagicMock(resumable_progress=50, total_size=100)
                return status, None
            return None, {"id": "VID"}

        request.next_chunk = next_chunk
        service = MagicMock()
        service.videos.return_value.insert.return_value = request
        service._http = fake_http

        with patch("clipcutter.youtube.get_authenticated_service",
                   return_value=(service, _fake_creds(access="OLD"))):
            result = upload_video(
                creds=_fake_creds(access="OLD"),
                file_path=fake_file,
                title="t", description="", tags=[], category_id="20",
                privacy="private",
                creds_path=creds_path,
            )

        assert result.success is True

        # The on-disk file now carries the refreshed token, so the next
        # clip in the batch (or the next session) starts authenticated.
        on_disk = json.loads(creds_path.read_text())
        assert on_disk["access_token"] == "NEW"

    def test_token_unchanged_does_not_rewrite_file(self, tmp_path):
        """No spurious writes when nothing actually refreshed."""
        fake_file = tmp_path / "clip.mp4"
        fake_file.write_bytes(b"x")
        creds_path = tmp_path / ".creds.json"

        # Pre-populate creds; record mtime to detect any rewrite.
        save_credentials(_fake_creds(access="STABLE"), creds_path)
        # Touch to a known old mtime so a rewrite would bump it.
        import os
        os.utime(creds_path, (1_000_000_000, 1_000_000_000))
        before_mtime = creds_path.stat().st_mtime

        fake_google_creds = MagicMock()
        fake_google_creds.token = "STABLE"  # never changes
        fake_google_creds.refresh_token = "r"
        fake_http = MagicMock()
        fake_http.credentials = fake_google_creds

        request = MagicMock()
        request.next_chunk = MagicMock(return_value=(None, {"id": "VID"}))
        service = MagicMock()
        service.videos.return_value.insert.return_value = request
        service._http = fake_http

        with patch("clipcutter.youtube.get_authenticated_service",
                   return_value=(service, _fake_creds(access="STABLE"))):
            upload_video(
                creds=_fake_creds(access="STABLE"),
                file_path=fake_file,
                title="t", description="", tags=[], category_id="20",
                privacy="private",
                creds_path=creds_path,
            )

        after_mtime = creds_path.stat().st_mtime
        assert before_mtime == after_mtime  # no rewrite

    def test_no_creds_path_is_no_op(self, tmp_path):
        """Backward compat: callers that don't pass creds_path see no
        persistence side effects (the cancel-tests rely on this)."""
        fake_file = tmp_path / "clip.mp4"
        fake_file.write_bytes(b"x")

        fake_google_creds = MagicMock()
        fake_google_creds.token = "NEW"
        fake_http = MagicMock()
        fake_http.credentials = fake_google_creds

        request = MagicMock()
        request.next_chunk = MagicMock(return_value=(None, {"id": "VID"}))
        service = MagicMock()
        service.videos.return_value.insert.return_value = request
        service._http = fake_http

        with patch("clipcutter.youtube.get_authenticated_service",
                   return_value=(service, _fake_creds(access="OLD"))):
            result = upload_video(
                creds=_fake_creds(access="OLD"),
                file_path=fake_file,
                title="t", description="", tags=[], category_id="20",
                privacy="private",
                # creds_path deliberately omitted
            )

        assert result.success is True


# ===========================================================================
# Sub-change 4: youtube_status RefreshError vs HttpError vs other Exception
# ===========================================================================

class TestYouTubeStatusErrorMapping:
    """RefreshError → expired (re-auth needed); HttpError → surface status;
    everything else → surface class + message so the user sees what broke
    rather than always being told "expired"."""

    def _seed_creds(self, app):
        """Write a valid creds file so load_credentials returns non-None."""
        from clipcutter.config import YOUTUBE_CREDENTIALS_FILE
        state = _recover_app_state(app)
        save_credentials(_fake_creds(), state.output_dir / YOUTUBE_CREDENTIALS_FILE)

    def test_no_creds_file_returns_authenticated_false(self, app_client):
        """Status with no creds at all is just "not signed in" — no error."""
        resp = app_client.get("/api/youtube/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["authenticated"] is False
        assert "error" not in body

    def test_refresh_error_maps_to_expired(self, app_client):
        self._seed_creds(app_client.app)
        # Make get_authenticated_service raise RefreshError (e.g., refresh
        # token revoked by the user via Google account settings).
        with patch("clipcutter.youtube.get_authenticated_service",
                   side_effect=FakeRefreshError("token revoked")):
            resp = app_client.get("/api/youtube/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["authenticated"] is False
        assert "expired" in body["error"].lower() or "invalid" in body["error"].lower()

    def test_http_error_surfaces_with_status(self, app_client):
        self._seed_creds(app_client.app)
        err = FakeHttpError(_http_response(403), b"forbidden")

        with patch("clipcutter.youtube.get_authenticated_service",
                   side_effect=err):
            resp = app_client.get("/api/youtube/status")
        assert resp.status_code == 200
        body = resp.json()
        # Still "authenticated" (creds are loadable, not actually expired) but
        # the user sees what actually went wrong.
        assert body["authenticated"] is True
        assert body["error_class"] == "HttpError"
        assert "403" in body["error"]

    def test_generic_exception_surfaces_class_and_message(self, app_client):
        """A library bug, a DNS failure, a TLS handshake error — anything
        that's not RefreshError/HttpError — must report what it actually
        was. The old code always said "expired credentials", which sent
        the user on a re-auth wild goose chase."""
        self._seed_creds(app_client.app)

        with patch("clipcutter.youtube.get_authenticated_service",
                   side_effect=RuntimeError("nameserver unreachable")):
            resp = app_client.get("/api/youtube/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["authenticated"] is True
        assert body["error_class"] == "RuntimeError"
        assert "nameserver" in body["error"]


# ===========================================================================
# Sub-change 5: HttpError 4xx permanent vs 5xx transient
# ===========================================================================

class TestUploadResultPermanentField:
    """UploadResult.permanent defaults False; upload_video sets True on
    HttpError 4xx (bad request, forbidden, quota, duplicate) and False on
    HttpError 5xx (server-side hiccup — retry might succeed)."""

    def test_permanent_field_defaults_false(self):
        r = UploadResult(success=True)
        assert r.permanent is False

    def _run_upload_with_error(self, tmp_path, err):
        """Helper: arrange upload_video to fail with the given exception."""
        fake_file = tmp_path / "clip.mp4"
        fake_file.write_bytes(b"x")

        def raising_next_chunk(num_retries=0):
            raise err

        request = MagicMock()
        request.next_chunk = raising_next_chunk
        service = MagicMock()
        service.videos.return_value.insert.return_value = request
        # Provide a _http with credentials so the finally-clause doesn't
        # blow up trying to read service._http.credentials.token.
        service._http = MagicMock(credentials=MagicMock(token="STABLE"))

        with patch("clipcutter.youtube.get_authenticated_service",
                   return_value=(service, _fake_creds(access="STABLE"))):
            return upload_video(
                creds=_fake_creds(access="STABLE"),
                file_path=fake_file,
                title="t", description="", tags=[], category_id="20",
                privacy="private",
            )

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 409, 429])
    def test_4xx_is_permanent(self, tmp_path, status_code):
        err = FakeHttpError(_http_response(status_code), b"client error")
        result = self._run_upload_with_error(tmp_path, err)
        assert result.success is False
        assert result.permanent is True
        assert result.error is not None

    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    def test_5xx_is_transient(self, tmp_path, status_code):
        err = FakeHttpError(_http_response(status_code), b"server error")
        result = self._run_upload_with_error(tmp_path, err)
        assert result.success is False
        assert result.permanent is False
        assert result.error is not None

    def test_non_http_exception_is_not_permanent(self, tmp_path):
        """A ConnectionResetError or socket timeout isn't an HttpError;
        the retry-might-help signal applies, so permanent=False."""
        result = self._run_upload_with_error(
            tmp_path, ConnectionResetError("socket"),
        )
        assert result.success is False
        assert result.permanent is False


# ===========================================================================
# Sub-change 6: add_to_playlist failure after a successful upload is logged
# ===========================================================================

class TestAddToPlaylistFailureLogsWarning:
    """When the upload succeeds but the follow-up add_to_playlist call
    fails (network blip, permission, deleted playlist), the failure must
    be visible — previously the catch was silent so the user had no clue
    why their clip didn't land in the requested playlist. The overall
    upload result is still "success" (non-fatal), but a logger.warning
    has to fire."""

    def test_playlist_add_failure_emits_warning(
        self, output_dir, app_client, caplog,
    ):
        import json
        import logging
        import time
        from clipcutter.config import YOUTUBE_CREDENTIALS_FILE

        # 1) Seed creds so the upload route doesn't 401.
        creds_path = output_dir / YOUTUBE_CREDENTIALS_FILE
        creds_path.parent.mkdir(parents=True, exist_ok=True)
        creds_path.write_text(json.dumps({
            "access_token": "a", "refresh_token": "r", "token_expiry": None,
            "client_id": "cid", "client_secret": "csec",
        }), encoding="utf-8")

        # 2) Seed a kept clip + metadata so the worker has something to upload.
        stem = "playlistfail"
        filename = "clip_001.mp4"
        kept_dir = output_dir / "clips" / "kept" / stem
        kept_dir.mkdir(parents=True, exist_ok=True)
        (kept_dir / filename).write_bytes(b"stub-kept-bytes")
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

        # 3) Patch upload_video to return success, add_to_playlist to raise.
        def fake_upload_video(
            *, creds, file_path, title, description, tags, category_id,
            privacy, progress_callback=None, cancel_event=None,
            creds_path=None,
        ):
            return UploadResult(
                success=True, video_id="VID123",
                url="https://www.youtube.com/watch?v=VID123",
            )

        def fake_add_to_playlist(creds, playlist_id, video_id):
            raise RuntimeError("playlist insert blew up")

        with caplog.at_level(logging.WARNING, logger="clipcutter.routes.youtube"):
            with patch(
                "clipcutter.youtube.upload_video", side_effect=fake_upload_video,
            ), patch(
                "clipcutter.youtube.add_to_playlist",
                side_effect=fake_add_to_playlist,
            ):
                resp = app_client.post("/api/youtube/upload", json={
                    "clips": [{
                        "video_stem": stem,
                        "filename": filename,
                        "use_encoded": False,
                        "title": "Test", "description": "", "tags": [],
                        "category_id": "20", "privacy": "private",
                        "playlist_id": "PL_FAKE",
                    }],
                })
                assert resp.status_code == 200, resp.text

                # Wait for the worker thread to finish.
                state = _recover_app_state(app_client.app)
                deadline = time.time() + 5.0
                while state.upl.snapshot()["running"] and time.time() < deadline:
                    time.sleep(0.02)

        snap = state.upl.snapshot()
        # Upload itself still succeeded — playlist-add failure is non-fatal.
        assert snap["running"] is False
        assert len(snap["completed"]) == 1
        assert snap["completed"][0]["video_id"] == "VID123"
        assert snap["errors"] == []

        # A WARNING-level log line on the routes.youtube logger must
        # mention the failure so the user can see it in the server log.
        warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and r.name == "clipcutter.routes.youtube"
        ]
        assert warnings, (
            "Expected at least one WARNING on clipcutter.routes.youtube; "
            f"got: {[(r.name, r.levelname, r.getMessage()) for r in caplog.records]}"
        )
        msgs = " ".join(r.getMessage() for r in warnings)
        assert "playlist" in msgs.lower()
        assert "playlist insert blew up" in msgs

    def test_playlist_add_success_emits_no_warning(
        self, output_dir, app_client, caplog,
    ):
        """Sanity: when add_to_playlist succeeds, no warning is logged —
        the warning is reserved for the actual failure path."""
        import json
        import logging
        import time
        from clipcutter.config import YOUTUBE_CREDENTIALS_FILE

        creds_path = output_dir / YOUTUBE_CREDENTIALS_FILE
        creds_path.parent.mkdir(parents=True, exist_ok=True)
        creds_path.write_text(json.dumps({
            "access_token": "a", "refresh_token": "r", "token_expiry": None,
            "client_id": "cid", "client_secret": "csec",
        }), encoding="utf-8")

        stem = "playlistok"
        filename = "clip_001.mp4"
        kept_dir = output_dir / "clips" / "kept" / stem
        kept_dir.mkdir(parents=True, exist_ok=True)
        (kept_dir / filename).write_bytes(b"stub-kept-bytes")
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

        def fake_upload_video(**kwargs):
            return UploadResult(
                success=True, video_id="VID456",
                url="https://www.youtube.com/watch?v=VID456",
            )

        with caplog.at_level(logging.WARNING, logger="clipcutter.routes.youtube"):
            with patch(
                "clipcutter.youtube.upload_video", side_effect=fake_upload_video,
            ), patch(
                "clipcutter.youtube.add_to_playlist", return_value=None,
            ):
                resp = app_client.post("/api/youtube/upload", json={
                    "clips": [{
                        "video_stem": stem,
                        "filename": filename,
                        "use_encoded": False,
                        "title": "Test", "description": "", "tags": [],
                        "category_id": "20", "privacy": "private",
                        "playlist_id": "PL_OK",
                    }],
                })
                assert resp.status_code == 200, resp.text

                state = _recover_app_state(app_client.app)
                deadline = time.time() + 5.0
                while state.upl.snapshot()["running"] and time.time() < deadline:
                    time.sleep(0.02)

        warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and r.name == "clipcutter.routes.youtube"
            and "playlist" in r.getMessage().lower()
        ]
        assert not warnings, (
            f"Expected no playlist warnings on success; got: "
            f"{[r.getMessage() for r in warnings]}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recover_app_state(app) -> AppState:
    """Walk route closures to find the AppState instance the routes were
    bound against — same trick the cancel-route test uses."""
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
