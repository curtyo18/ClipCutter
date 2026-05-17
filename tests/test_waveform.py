"""Tests for the waveform API endpoint."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.conftest import create_pending_clip, save_test_metadata


class TestWaveformEndpoint:
    """GET /api/waveform/{video_stem}/{filename} returns waveform data."""

    def test_waveform_returns_data(self, output_dir, app_client):
        stem = "wftest"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/wftest.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/wftest.mp4")

        resp = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        assert resp.status_code == 200

        data = resp.json()
        assert "waveform" in data
        assert "duration" in data
        assert "sample_count" in data
        assert isinstance(data["waveform"], list)
        assert len(data["waveform"]) > 0
        assert data["duration"] > 0

    def test_waveform_values_normalized(self, output_dir, app_client):
        stem = "wfnorm"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/wfnorm.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/wfnorm.mp4")

        resp = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        data = resp.json()

        for val in data["waveform"]:
            assert 0.0 <= val <= 1.0, f"Waveform value {val} out of [0,1] range"

    def test_waveform_caches_sidecar(self, output_dir, app_client):
        stem = "wfcache"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/wfcache.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/wfcache.mp4")

        # First request generates cache
        resp1 = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        assert resp1.status_code == 200

        # Check sidecar file was created
        clip_path = output_dir / "clips" / "pending" / stem / "clip_001.mp4"
        cache_path = clip_path.with_suffix(".mp4.waveform.json")
        assert cache_path.exists(), "Waveform cache sidecar should exist"

        # Second request uses cache
        resp2 = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        assert resp2.status_code == 200
        assert resp2.json()["waveform"] == resp1.json()["waveform"]

    def test_waveform_includes_highlight_regions(self, output_dir, app_client):
        stem = "wfregions"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/wfregions.mp4",
        )
        # Manually add highlight_regions to the clip metadata
        clip.highlight_regions = [
            {"offset": 0.5, "duration": 1.0, "type": "volume_spike", "confidence": 0.85}
        ]
        save_test_metadata(output_dir, stem, [clip], "/fake/wfregions.mp4")

        resp = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        data = resp.json()
        assert "highlight_regions" in data
        assert len(data["highlight_regions"]) == 1
        assert data["highlight_regions"][0]["type"] == "volume_spike"

    def test_waveform_404_for_missing_clip(self, output_dir, app_client):
        resp = app_client.get("/api/waveform/noexist/fake.mp4")
        assert resp.status_code == 404


class TestWaveformErrorPaths:
    """The waveform route maps three failure modes to HTTP 500:
      - ffmpeg returns non-zero (audio extraction failed)
      - ffmpeg returns zero but no audio data (silent / unreadable)
      - ffmpeg call times out (subprocess.TimeoutExpired)
    Each branch is exercised by mocking subprocess.run so we don't depend
    on a real ffmpeg binary being on PATH."""

    def _setup_pending_stub(self, output_dir: Path, stem: str, filename: str) -> Path:
        """Write a stub clip (not a valid mp4) + metadata so the route
        passes the existence check. The downstream ffmpeg call is mocked,
        so the stub bytes never get parsed."""
        pending_dir = output_dir / "clips" / "pending" / stem
        pending_dir.mkdir(parents=True, exist_ok=True)
        clip_path = pending_dir / filename
        clip_path.write_bytes(b"stub-bytes")

        meta_dir = output_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / f"{stem}_clips.json").write_text(json.dumps({
            "source_video": f"/fake/{stem}.mp4",
            "processed_at": "2026-01-01T00:00:00",
            "clip_count": 1,
            "clips": [{
                "filename": filename,
                "source_video": f"/fake/{stem}.mp4",
                "start_time": 0.0, "end_time": 1.0, "duration": 1.0,
                "detection_reasons": ["volume_spike"], "confidence": 0.8,
                "status": "pending",
            }],
        }), encoding="utf-8")
        return clip_path

    def test_ffmpeg_nonzero_returns_500(self, output_dir, app_client):
        """If ffmpeg exits non-zero (corrupt input, decoder error), the
        route returns 500 with a clear "extraction failed" message rather
        than an unhandled stack trace."""
        stem = "wf_fail"
        self._setup_pending_stub(output_dir, stem, "clip_001.mp4")

        fake_result = MagicMock(returncode=1, stdout=b"", stderr=b"ffmpeg error")
        with patch(
            "clipcutter.routes.review.subprocess.run", return_value=fake_result,
        ):
            resp = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        assert resp.status_code == 500
        assert "extraction failed" in resp.json()["detail"].lower()

    def test_ffmpeg_returns_no_audio_returns_500(self, output_dir, app_client):
        """ffmpeg succeeded (returncode=0) but produced zero bytes — e.g.
        the input has no audio track. Route maps this to 500 with a
        distinct "no audio data" message."""
        stem = "wf_noaudio"
        self._setup_pending_stub(output_dir, stem, "clip_001.mp4")

        fake_result = MagicMock(returncode=0, stdout=b"", stderr=b"")
        with patch(
            "clipcutter.routes.review.subprocess.run", return_value=fake_result,
        ):
            resp = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        assert resp.status_code == 500
        assert "no audio" in resp.json()["detail"].lower()

    def test_ffmpeg_timeout_returns_500(self, output_dir, app_client):
        """If ffmpeg pins (e.g. corrupt header, network input), the
        subprocess.run timeout fires and the route surfaces a 500 with
        a "timed out" message rather than hanging the request thread."""
        stem = "wf_timeout"
        self._setup_pending_stub(output_dir, stem, "clip_001.mp4")

        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30)

        with patch(
            "clipcutter.routes.review.subprocess.run", side_effect=raise_timeout,
        ):
            resp = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        assert resp.status_code == 500
        assert "timed out" in resp.json()["detail"].lower()


class TestClipsEndpointHighlightRegions:
    """GET /api/clips returns highlight_regions in clip data."""

    def test_clips_include_highlight_regions(self, output_dir, app_client):
        stem = "hrtest"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/hrtest.mp4",
        )
        clip.highlight_regions = [
            {"offset": 1.0, "duration": 2.0, "type": "laughter", "confidence": 0.7}
        ]
        save_test_metadata(output_dir, stem, [clip], "/fake/hrtest.mp4")

        resp = app_client.get("/api/clips")
        data = resp.json()
        assert data["total"] == 1
        assert len(data["clips"][0]["highlight_regions"]) == 1
