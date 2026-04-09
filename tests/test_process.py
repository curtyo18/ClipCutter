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


class TestFolderScan:
    """GET /api/folder-scan — scan a folder for video files."""

    def test_folder_not_found_returns_400(self, output_dir, app_client):
        resp = app_client.get("/api/folder-scan?folder=/nonexistent/path/xyz")
        assert resp.status_code == 400

    def test_empty_folder_returns_empty_list(self, output_dir, app_client, tmp_path):
        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["videos"] == []
        assert data["total_size_mb"] == 0.0

    def test_unprocessed_video_has_unprocessed_status(self, output_dir, app_client, tmp_path):
        video = tmp_path / "clip_001.mp4"
        video.write_bytes(b"\x00" * 100 * 1024)  # 100 KB so size_mb rounds to > 0.0

        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["videos"]) == 1
        v = data["videos"][0]
        assert v["filename"] == "clip_001.mp4"
        assert v["status"] == "unprocessed"
        assert v["size_mb"] > 0
        assert v["age_days"] >= 0

    def test_non_video_files_excluded(self, output_dir, app_client, tmp_path):
        (tmp_path / "notes.txt").write_text("hello")
        (tmp_path / "thumb.jpg").write_bytes(b"\xff")
        (tmp_path / "game.mp4").write_bytes(b"\x00" * 512)

        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        data = resp.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["filename"] == "game.mp4"

    def test_processed_video_has_processed_status(self, output_dir, app_client, tmp_path):
        from tests.conftest import save_test_metadata
        from clipcutter.models import ClipMetadata

        video = tmp_path / "session_001.mp4"
        video.write_bytes(b"\x00" * 512)

        clip = ClipMetadata(
            filename="clip_001.mp4",
            source_video=str(video),
            start_time=0.0, end_time=5.0, duration=5.0,
            detection_reasons=["volume_spike"], confidence=0.8,
            status="kept",
        )
        save_test_metadata(output_dir, "session_001", [clip], str(video))

        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        data = resp.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["status"] == "processed"

    def test_pending_review_video_has_pending_review_status(self, output_dir, app_client, tmp_path):
        from tests.conftest import save_test_metadata, create_pending_clip

        video = tmp_path / "session_002.mp4"
        video.write_bytes(b"\x00" * 512)

        clip = create_pending_clip(output_dir, "session_002", "clip_001.mp4",
                                   source_video=str(video))
        save_test_metadata(output_dir, "session_002", [clip], str(video))

        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        data = resp.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["status"] == "pending_review"

    def test_total_size_sums_all_videos(self, output_dir, app_client, tmp_path):
        (tmp_path / "a.mp4").write_bytes(b"\x00" * 1024 * 1024)
        (tmp_path / "b.mkv").write_bytes(b"\x00" * 2 * 1024 * 1024)

        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        data = resp.json()
        assert len(data["videos"]) == 2
        assert data["total_size_mb"] == pytest.approx(3.0, abs=0.1)

    def test_same_stem_different_folder_treated_as_unprocessed(self, output_dir, app_client, tmp_path):
        """Metadata for a same-named file from a different folder should not affect status."""
        from tests.conftest import save_test_metadata
        from clipcutter.models import ClipMetadata

        video = tmp_path / "game.mp4"
        video.write_bytes(b"\x00" * 512)

        # Metadata points to a different folder's game.mp4
        other_path = "/other/folder/game.mp4"
        clip = ClipMetadata(
            filename="clip_001.mp4", source_video=other_path,
            start_time=0.0, end_time=5.0, duration=5.0,
            detection_reasons=["volume_spike"], confidence=0.8,
            status="kept",
        )
        save_test_metadata(output_dir, "game", [clip], other_path)

        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        data = resp.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["status"] == "unprocessed"


class TestFolderFileDelete:
    """POST /api/folder-scan/file/delete — delete a source video file."""

    def test_file_not_found_returns_404(self, output_dir, app_client, tmp_path):
        resp = app_client.post("/api/folder-scan/file/delete", json={
            "folder": str(tmp_path),
            "filename": "nonexistent.mp4",
        })
        assert resp.status_code == 404

    def test_path_traversal_returns_400(self, output_dir, app_client, tmp_path):
        resp = app_client.post("/api/folder-scan/file/delete", json={
            "folder": str(tmp_path),
            "filename": "../outside.mp4",
        })
        assert resp.status_code == 400

    def test_pending_clips_blocks_delete(self, output_dir, app_client, tmp_path):
        from tests.conftest import save_test_metadata, create_pending_clip

        video = tmp_path / "session_003.mp4"
        video.write_bytes(b"\x00" * 512)

        clip = create_pending_clip(output_dir, "session_003", "clip_001.mp4",
                                   source_video=str(video))
        save_test_metadata(output_dir, "session_003", [clip], str(video))

        resp = app_client.post("/api/folder-scan/file/delete", json={
            "folder": str(tmp_path),
            "filename": "session_003.mp4",
        })
        assert resp.status_code == 400
        assert video.exists()  # File was not deleted

    def test_delete_unprocessed_video(self, output_dir, app_client, tmp_path):
        video = tmp_path / "old_game.mp4"
        video.write_bytes(b"\x00" * 1024 * 1024)  # 1 MB

        resp = app_client.post("/api/folder-scan/file/delete", json={
            "folder": str(tmp_path),
            "filename": "old_game.mp4",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["freed_mb"] == pytest.approx(1.0, abs=0.1)
        assert not video.exists()

    def test_delete_processed_video(self, output_dir, app_client, tmp_path):
        from tests.conftest import save_test_metadata
        from clipcutter.models import ClipMetadata

        video = tmp_path / "session_done.mp4"
        video.write_bytes(b"\x00" * 512)

        clip = ClipMetadata(
            filename="clip_001.mp4", source_video=str(video),
            start_time=0.0, end_time=5.0, duration=5.0,
            detection_reasons=["volume_spike"], confidence=0.8,
            status="kept",
        )
        save_test_metadata(output_dir, "session_done", [clip], str(video))

        resp = app_client.post("/api/folder-scan/file/delete", json={
            "folder": str(tmp_path),
            "filename": "session_done.mp4",
        })
        assert resp.status_code == 200
        assert not video.exists()
