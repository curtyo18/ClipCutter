"""Tests for Task 11: subprocess and HTTP timeouts.

Verifies that every ffmpeg/ffprobe call in audio.py, encoder.py,
compiler.py — plus the encode worker's Popen.communicate — passes a
timeout and re-raises subprocess.TimeoutExpired as FFmpegTimeoutError.

ffmpeg isn't required in the test container; subprocess.run /
subprocess.Popen are patched and their side_effect raises
TimeoutExpired so the wrapping behavior can be exercised in isolation.
"""

import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clipcutter import audio, compiler, encoder
from clipcutter.errors import FFmpegTimeoutError


# ---------------------------------------------------------------------------
# audio.py
# ---------------------------------------------------------------------------

class TestAudioTimeouts:
    """audio.py's ffmpeg/ffprobe wrappers must pass timeout= and wrap
    TimeoutExpired in FFmpegTimeoutError."""

    def test_get_video_duration_passes_timeout(self, tmp_path):
        fake = MagicMock()
        fake.stdout = "12.34\n"
        with patch("clipcutter.audio.subprocess.run", return_value=fake) as mrun:
            audio.get_video_duration(tmp_path / "vid.mp4")
        kwargs = mrun.call_args.kwargs
        assert kwargs.get("timeout") == audio.FFPROBE_TIMEOUT

    def test_get_video_duration_wraps_timeout_expired(self, tmp_path):
        with patch(
            "clipcutter.audio.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30),
        ):
            with pytest.raises(FFmpegTimeoutError, match="ffprobe timed out"):
                audio.get_video_duration(tmp_path / "vid.mp4")

    def test_extract_audio_probe_passes_timeout(self, tmp_path):
        video = tmp_path / "vid.mp4"
        video.write_bytes(b"x")  # non-empty so the mtime path exists
        out = tmp_path / "out"

        probe = MagicMock()
        probe.stdout = "audio\n"
        extract = MagicMock()

        with patch(
            "clipcutter.audio.subprocess.run",
            side_effect=[probe, extract],
        ) as mrun:
            audio.extract_audio(video, out)

        # First call is ffprobe (30s), second is ffmpeg extract (600s).
        first, second = mrun.call_args_list
        assert first.kwargs.get("timeout") == audio.FFPROBE_TIMEOUT
        assert second.kwargs.get("timeout") == audio.FFMPEG_EXTRACT_AUDIO_TIMEOUT

    def test_extract_audio_wraps_probe_timeout(self, tmp_path):
        video = tmp_path / "vid.mp4"
        video.write_bytes(b"x")
        out = tmp_path / "out"

        with patch(
            "clipcutter.audio.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30),
        ):
            with pytest.raises(FFmpegTimeoutError, match="ffprobe timed out"):
                audio.extract_audio(video, out)

    def test_extract_audio_wraps_extract_timeout(self, tmp_path):
        video = tmp_path / "vid.mp4"
        video.write_bytes(b"x")
        out = tmp_path / "out"

        probe = MagicMock()
        probe.stdout = "audio\n"

        with patch(
            "clipcutter.audio.subprocess.run",
            side_effect=[
                probe,
                subprocess.TimeoutExpired(cmd="ffmpeg", timeout=600),
            ],
        ):
            with pytest.raises(FFmpegTimeoutError, match="ffmpeg timed out"):
                audio.extract_audio(video, out)

    def test_extract_clip_passes_timeout(self, tmp_path):
        video = tmp_path / "vid.mp4"
        video.write_bytes(b"x")
        out = tmp_path / "clip.mp4"
        result = MagicMock()
        result.returncode = 0
        with patch(
            "clipcutter.audio.subprocess.run", return_value=result
        ) as mrun:
            audio.extract_clip(video, 0.0, 1.0, out)
        assert mrun.call_args.kwargs.get("timeout") == audio.FFMPEG_EXTRACT_CLIP_TIMEOUT

    def test_extract_clip_wraps_copy_timeout(self, tmp_path):
        video = tmp_path / "vid.mp4"
        video.write_bytes(b"x")
        out = tmp_path / "clip.mp4"
        with patch(
            "clipcutter.audio.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60),
        ):
            with pytest.raises(FFmpegTimeoutError, match="extracting clip"):
                audio.extract_clip(video, 0.0, 1.0, out)

    def test_extract_clip_wraps_reencode_timeout(self, tmp_path):
        """If stream copy returns non-zero, the re-encode fallback also
        gets a timeout that wraps to FFmpegTimeoutError."""
        video = tmp_path / "vid.mp4"
        video.write_bytes(b"x")
        out = tmp_path / "clip.mp4"

        copy_fail = MagicMock()
        copy_fail.returncode = 1

        with patch(
            "clipcutter.audio.subprocess.run",
            side_effect=[
                copy_fail,
                subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60),
            ],
        ):
            with pytest.raises(FFmpegTimeoutError, match="re-encoding clip"):
                audio.extract_clip(video, 0.0, 1.0, out)


# ---------------------------------------------------------------------------
# encoder.py
# ---------------------------------------------------------------------------

class TestEncoderTimeouts:
    """encoder.encode_clip() passes timeout= and wraps TimeoutExpired."""

    def _preset_with_args(self):
        # Anything non-empty so build_encode_command emits an ffmpeg cmd
        # rather than returning None for a straight copy.
        from clipcutter.encoder import EncodingPreset

        return EncodingPreset(
            name="test",
            display_name="Test",
            extension=".mp4",
            ffmpeg_args=["-c:v", "libx264", "-crf", "18"],
        )

    def test_encode_clip_passes_timeout(self, tmp_path):
        inp = tmp_path / "in.mp4"
        inp.write_bytes(b"x")
        outp = tmp_path / "out.mp4"
        with patch("clipcutter.encoder.subprocess.run") as mrun:
            encoder.encode_clip(inp, outp, self._preset_with_args())
        assert mrun.call_args.kwargs.get("timeout") == encoder.FFMPEG_ENCODE_TIMEOUT

    def test_encode_clip_wraps_timeout(self, tmp_path):
        inp = tmp_path / "in.mp4"
        inp.write_bytes(b"x")
        outp = tmp_path / "out.mp4"
        with patch(
            "clipcutter.encoder.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=600),
        ):
            with pytest.raises(FFmpegTimeoutError, match="encoding timed out"):
                encoder.encode_clip(inp, outp, self._preset_with_args())

    def test_encode_clip_timeout_cleans_partial_output(self, tmp_path):
        inp = tmp_path / "in.mp4"
        inp.write_bytes(b"x")
        outp = tmp_path / "out.mp4"
        outp.write_bytes(b"half-written")

        with patch(
            "clipcutter.encoder.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=600),
        ):
            with pytest.raises(FFmpegTimeoutError):
                encoder.encode_clip(inp, outp, self._preset_with_args())

        assert not outp.exists(), "partial output should be deleted on timeout"


# ---------------------------------------------------------------------------
# compiler.py
# ---------------------------------------------------------------------------

class TestCompilerTimeouts:
    """compiler._build_concat / _build_crossfade pass timeout= and wrap."""

    def test_build_concat_passes_timeout(self, tmp_path):
        clips = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
        for c in clips:
            c.write_bytes(b"x")
        out = tmp_path / "comp.mp4"

        result = MagicMock()
        result.returncode = 0
        with patch(
            "clipcutter.compiler.subprocess.run", return_value=result
        ) as mrun:
            compiler.build_compilation(clips, out, transition="cut")
        assert mrun.call_args.kwargs.get("timeout") == compiler.FFMPEG_COMPILE_TIMEOUT

    def test_build_concat_wraps_timeout(self, tmp_path):
        clips = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
        for c in clips:
            c.write_bytes(b"x")
        out = tmp_path / "comp.mp4"

        with patch(
            "clipcutter.compiler.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1800),
        ):
            with pytest.raises(FFmpegTimeoutError, match="concat timed out"):
                compiler.build_compilation(clips, out, transition="cut")

    def test_build_crossfade_wraps_timeout(self, tmp_path):
        clips = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
        for c in clips:
            c.write_bytes(b"x")
        out = tmp_path / "comp.mp4"

        # _build_crossfade calls get_video_duration first; make that
        # succeed via a stub on the audio module, then have the actual
        # ffmpeg crossfade raise TimeoutExpired.
        with patch(
            "clipcutter.compiler.get_video_duration",
            return_value=1.0,
        ):
            with patch(
                "clipcutter.compiler.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1800),
            ):
                with pytest.raises(FFmpegTimeoutError, match="crossfade timed out"):
                    compiler.build_compilation(
                        clips, out, transition="crossfade",
                    )


# ---------------------------------------------------------------------------
# routes/encode.py worker — Popen.communicate timeout
# ---------------------------------------------------------------------------

class TestEncodeWorkerTimeout:
    """The encode worker's Popen.communicate uses FFMPEG_ENCODE_TIMEOUT
    and surfaces a TimeoutExpired as a per-clip error so the batch
    advances rather than crashing the worker."""

    @staticmethod
    def _setup_kept_clip(output_dir: Path, stem: str, filename: str) -> Path:
        """Place a fake kept clip + minimal metadata on disk without
        running ffmpeg. The encode route only stat()s the kept file
        (no decode) before queuing the batch, so bytes can be anything."""
        kept_dir = output_dir / "clips" / "kept" / stem
        kept_dir.mkdir(parents=True, exist_ok=True)
        kept_path = kept_dir / filename
        kept_path.write_bytes(b"fake-kept-bytes")

        meta_dir = output_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        meta_path = meta_dir / f"{stem}_clips.json"
        import json
        meta_path.write_text(json.dumps({
            "source_video": f"/fake/{stem}.mp4",
            "processed_at": "2026-01-01T00:00:00",
            "clip_count": 1,
            "clips": [{
                "filename": filename,
                "source_video": f"/fake/{stem}.mp4",
                "start_time": 0.0,
                "end_time": 2.0,
                "duration": 2.0,
                "detection_reasons": ["volume_spike"],
                "confidence": 0.8,
                "status": "kept",
            }],
        }), encoding="utf-8")
        return kept_path

    def test_worker_communicate_uses_ffmpeg_encode_timeout(
        self, output_dir, app_client
    ):
        stem = "timeout_vid"
        filename = "clip_001.mp4"
        self._setup_kept_clip(output_dir, stem, filename)

        observed_timeouts: list = []

        class FakeProc:
            def __init__(self):
                self.returncode = 0

            def communicate(self, timeout=None):
                observed_timeouts.append(timeout)
                return ("", "")

            def kill(self):
                pass

        def fake_popen(*args, **kwargs):
            # Materialise the output file so post-encode checks pass.
            cmd = args[0]
            output_arg = Path(cmd[-1])
            output_arg.parent.mkdir(parents=True, exist_ok=True)
            output_arg.write_bytes(b"fake-encoded")
            return FakeProc()

        with patch("clipcutter.routes.encode.subprocess.Popen", side_effect=fake_popen):
            resp = app_client.post(
                "/api/encode",
                json={
                    "clips": [{"video_stem": stem, "filename": filename}],
                    "preset": "high",
                },
            )
            assert resp.status_code == 200, resp.text

            deadline = time.time() + 5
            while time.time() < deadline:
                s = app_client.get("/api/encode/status").json()
                if not s["running"]:
                    break
                time.sleep(0.05)

        from clipcutter.encoder import FFMPEG_ENCODE_TIMEOUT
        assert observed_timeouts, "worker never called communicate()"
        assert FFMPEG_ENCODE_TIMEOUT in observed_timeouts

    def test_worker_timeout_then_second_clip_succeeds(
        self, output_dir, app_client
    ):
        """A two-clip batch where the first clip's Popen.communicate
        raises TimeoutExpired must not crash the worker — the second
        clip should still encode and land in metadata.

        Single-clip batches can't prove this (a per-clip error and a
        worker crash both leave the batch with the same "errors=[..]"
        footprint), so the only way to verify "batch survives" is to
        check that work continues after the failure.
        """
        stem = "timeout_batch"
        bad_filename = "clip_001.mp4"
        good_filename = "clip_002.mp4"
        self._setup_kept_clip(output_dir, stem, bad_filename)

        # Second clip: add a second entry into the same metadata file
        # so update_clip_encoding finds it.
        kept_dir = output_dir / "clips" / "kept" / stem
        (kept_dir / good_filename).write_bytes(b"fake-kept-bytes-2")
        meta_path = output_dir / "metadata" / f"{stem}_clips.json"
        import json as _json
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        meta["clip_count"] = 2
        meta["clips"].append({
            "filename": good_filename,
            "source_video": f"/fake/{stem}.mp4",
            "start_time": 2.0,
            "end_time": 4.0,
            "duration": 2.0,
            "detection_reasons": ["volume_spike"],
            "confidence": 0.8,
            "status": "kept",
        })
        meta_path.write_text(_json.dumps(meta), encoding="utf-8")

        # Each Popen call gets its own FakeProc; the first one (for the
        # bad clip) raises TimeoutExpired on its initial communicate,
        # the second (for the good clip) returns success.
        proc_calls: list = []

        class TimeoutFakeProc:
            def __init__(self):
                self.returncode = -9
                self._calls = 0

            def communicate(self, timeout=None):
                self._calls += 1
                if self._calls == 1:
                    raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
                return ("", "")

            def kill(self):
                pass

        class SuccessFakeProc:
            def __init__(self, output_path: Path):
                self.returncode = 0
                self._output_path = output_path

            def communicate(self, timeout=None):
                # Materialise the encoded file so post-encode checks pass.
                self._output_path.parent.mkdir(parents=True, exist_ok=True)
                self._output_path.write_bytes(b"fake-encoded")
                return ("", "")

            def kill(self):
                pass

        def fake_popen(*args, **kwargs):
            cmd = args[0]
            output_arg = Path(cmd[-1])
            proc_calls.append(output_arg.name)
            # First call is the first clip → timeout; second is success.
            if len(proc_calls) == 1:
                return TimeoutFakeProc()
            return SuccessFakeProc(output_arg)

        with patch("clipcutter.routes.encode.subprocess.Popen", side_effect=fake_popen):
            resp = app_client.post(
                "/api/encode",
                json={
                    "clips": [
                        {"video_stem": stem, "filename": bad_filename},
                        {"video_stem": stem, "filename": good_filename},
                    ],
                    "preset": "high",
                },
            )
            assert resp.status_code == 200, resp.text

            deadline = time.time() + 5
            final = None
            while time.time() < deadline:
                final = app_client.get("/api/encode/status").json()
                if not final["running"]:
                    break
                time.sleep(0.05)

        assert final is not None
        assert final["running"] is False

        # Worker advanced past the timeout: both Popens fired.
        assert len(proc_calls) == 2, f"expected 2 Popen calls, got {proc_calls!r}"

        # First clip in errors with a "timed out" message.
        assert any(
            e.get("filename") == bad_filename
            and "timed out" in (e.get("error") or "").lower()
            for e in final["errors"]
        ), f"bad clip should be in errors with a timeout message: {final['errors']!r}"

        # Second clip completed (not in errors).
        assert good_filename in final["completed"], (
            f"good clip should have completed after the first's timeout: "
            f"completed={final['completed']!r} errors={final['errors']!r}"
        )

        # And the second clip's encoded_filename landed in metadata.
        from clipcutter.metadata import load_metadata
        clip_metas = load_metadata(meta_path)
        good_meta = next(c for c in clip_metas if c.filename == good_filename)
        assert good_meta.encoded_filename, (
            "encoded_filename should be set on the second clip after batch advance"
        )


# ---------------------------------------------------------------------------
# youtube.py OAuth requests.post
# ---------------------------------------------------------------------------

class TestYouTubeOAuthTimeouts:
    """exchange_code and refresh_access_token must pass timeout= to
    requests.post so a wedged Google token endpoint can't pin a
    FastAPI request thread."""

    def test_exchange_code_passes_timeout(self):
        from clipcutter import youtube

        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
        }
        fake_resp.raise_for_status = MagicMock()

        with patch("requests.post", return_value=fake_resp) as mpost:
            youtube.exchange_code("code", "cid", "csec", "https://cb")

        assert mpost.call_args.kwargs.get("timeout") == youtube.OAUTH_HTTP_TIMEOUT_SECONDS

    def test_refresh_access_token_passes_timeout(self):
        from clipcutter import youtube

        creds = youtube.YouTubeCredentials(
            access_token="old", refresh_token="rt",
            token_expiry=None, client_id="cid", client_secret="csec",
        )
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"access_token": "new", "expires_in": 3600}
        fake_resp.raise_for_status = MagicMock()

        with patch("requests.post", return_value=fake_resp) as mpost:
            youtube.refresh_access_token(creds)

        assert mpost.call_args.kwargs.get("timeout") == youtube.OAUTH_HTTP_TIMEOUT_SECONDS
