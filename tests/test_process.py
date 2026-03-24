"""Tests for the clip detection pipeline (Scenario 1 partial + Scenario 5 setup)."""

import shutil
import time
from pathlib import Path

import pytest


class TestSilenceProducesNoClips:
    """Silent audio should produce zero highlights / zero clips."""

    def test_silence_fallback_only(self, silence_video, output_dir, app_client):
        """Silent audio produces no real highlights, only a low-confidence
        fallback clip (last N seconds).  This is by-design behaviour."""
        proc_dir = output_dir / "source"
        proc_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(silence_video), str(proc_dir / "silence_5s.mp4"))

        resp = app_client.post("/api/process", json={
            "folder": str(proc_dir),
            "sensitivity": 1.0,
        })
        assert resp.status_code == 200
        _wait_processing(app_client)

        clips_resp = app_client.get("/api/clips")
        assert clips_resp.status_code == 200
        data = clips_resp.json()
        # Pipeline creates a fallback clip when nothing is detected
        assert data["total"] == 1, "Silent video should produce exactly 1 fallback clip"
        clip = data["clips"][0]
        assert "fallback" in clip["detection_reasons"], (
            "The only clip from a silent video should be a fallback"
        )


class TestNoiseProducesClips:
    """Loud white noise should trigger detection and produce clips."""

    def test_noise_produces_clips(self, noise_video, output_dir, app_client):
        proc_dir = output_dir / "source"
        proc_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(noise_video), str(proc_dir / "noise_10s.mp4"))

        resp = app_client.post("/api/process", json={
            "folder": str(proc_dir),
            "sensitivity": 1.0,
        })
        assert resp.status_code == 200
        _wait_processing(app_client)

        clips_resp = app_client.get("/api/clips")
        data = clips_resp.json()
        assert data["total"] >= 1, (
            "Loud noise video should produce at least 1 clip"
        )


class TestMixedVideoWorkflow:
    """Mixed video (silence-noise-silence) should detect the noisy section."""

    def test_mixed_produces_clips(self, mixed_video, output_dir, app_client):
        proc_dir = output_dir / "source"
        proc_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(mixed_video), str(proc_dir / "mixed_10s.mp4"))

        resp = app_client.post("/api/process", json={
            "folder": str(proc_dir),
            "sensitivity": 1.0,
        })
        assert resp.status_code == 200
        _wait_processing(app_client)

        clips_resp = app_client.get("/api/clips")
        data = clips_resp.json()
        assert data["total"] >= 1, (
            "Mixed video should produce at least 1 clip from the noisy section"
        )

        # Verify clips appear in pending directory
        pending = output_dir / "clips" / "pending"
        assert pending.exists(), "Pending clips directory should exist"
        video_dirs = list(pending.iterdir())
        assert len(video_dirs) >= 1, "Should have at least one video stem dir"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_processing(client, timeout: float = 120.0):
    """Poll /api/process/status until processing completes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get("/api/process/status").json()
        if not status.get("running", False):
            if status.get("error"):
                pytest.fail(f"Processing failed: {status['error']}")
            return
        time.sleep(0.5)
    pytest.fail("Processing timed out")
