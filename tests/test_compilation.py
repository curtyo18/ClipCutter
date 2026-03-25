"""Tests for the compilation builder API."""

import json
import time
from pathlib import Path

from tests.conftest import create_pending_clip, save_test_metadata


def _keep_clip(app_client, stem, filename):
    """Helper: keep a pending clip so it's available for compilation."""
    app_client.post(
        f"/api/clips/{stem}/{filename}/keep",
        json={"trim_start": 0.0, "trim_end": 0.0},
    )


def _setup_kept_clips(output_dir, app_client, stem, count=3):
    """Create and keep multiple clips for compilation testing."""
    clips = []
    for i in range(1, count + 1):
        fname = f"clip_{i:03d}.mp4"
        clip = create_pending_clip(
            output_dir, stem, fname,
            source_video=f"/fake/{stem}.mp4",
            confidence=0.9 - i * 0.1,
        )
        clips.append(clip)
    save_test_metadata(output_dir, stem, clips, f"/fake/{stem}.mp4")

    for clip in clips:
        _keep_clip(app_client, stem, clip.filename)

    return clips


def _wait_for_compilation(app_client, timeout=30):
    """Poll compilation status until complete or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        resp = app_client.get("/api/compilation/status")
        data = resp.json()
        if not data["running"]:
            return data
        time.sleep(0.5)
    raise TimeoutError("Compilation did not complete in time")


class TestCompilationBuild:
    """POST /api/compilation builds a compilation video."""

    def test_hard_cut_compilation(self, output_dir, app_client):
        stem = "comptest1"
        clips = _setup_kept_clips(output_dir, app_client, stem, count=2)

        resp = app_client.post("/api/compilation", json={
            "clips": [
                {"video_stem": stem, "filename": clips[0].filename},
                {"video_stem": stem, "filename": clips[1].filename},
            ],
            "transition": "cut",
            "title": "test_hardcut",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

        result = _wait_for_compilation(app_client)
        assert result["completed"] is True
        assert result["error"] is None
        assert result["output_filename"] is not None

        # Check file exists
        comp_path = output_dir / "clips" / "compilations" / result["output_filename"]
        assert comp_path.exists()

    def test_crossfade_compilation(self, output_dir, app_client):
        stem = "comptest2"
        clips = _setup_kept_clips(output_dir, app_client, stem, count=2)

        resp = app_client.post("/api/compilation", json={
            "clips": [
                {"video_stem": stem, "filename": clips[0].filename},
                {"video_stem": stem, "filename": clips[1].filename},
            ],
            "transition": "crossfade",
            "crossfade_duration": 0.3,
            "title": "test_crossfade",
        })
        assert resp.status_code == 200

        result = _wait_for_compilation(app_client)
        assert result["completed"] is True
        assert result["error"] is None

    def test_compilation_needs_at_least_2_clips(self, output_dir, app_client):
        stem = "comptest3"
        clips = _setup_kept_clips(output_dir, app_client, stem, count=1)

        resp = app_client.post("/api/compilation", json={
            "clips": [{"video_stem": stem, "filename": clips[0].filename}],
            "transition": "cut",
        })
        assert resp.status_code == 400

    def test_compilation_404_for_missing_clip(self, output_dir, app_client):
        resp = app_client.post("/api/compilation", json={
            "clips": [
                {"video_stem": "nope", "filename": "fake1.mp4"},
                {"video_stem": "nope", "filename": "fake2.mp4"},
            ],
            "transition": "cut",
        })
        assert resp.status_code == 404


class TestCompilationList:
    """GET /api/compilations returns completed compilations."""

    def test_list_compilations(self, output_dir, app_client):
        stem = "complist"
        clips = _setup_kept_clips(output_dir, app_client, stem, count=2)

        app_client.post("/api/compilation", json={
            "clips": [
                {"video_stem": stem, "filename": clips[0].filename},
                {"video_stem": stem, "filename": clips[1].filename},
            ],
            "transition": "cut",
            "title": "listtest",
        })
        _wait_for_compilation(app_client)

        resp = app_client.get("/api/compilations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["compilations"]) >= 1
        comp = data["compilations"][-1]
        assert comp["status"] == "complete"
        assert comp["file_exists"] is True


class TestCompilationDelete:
    """DELETE /api/compilation/{id} removes compilation."""

    def test_delete_compilation(self, output_dir, app_client):
        stem = "compdel"
        clips = _setup_kept_clips(output_dir, app_client, stem, count=2)

        resp = app_client.post("/api/compilation", json={
            "clips": [
                {"video_stem": stem, "filename": clips[0].filename},
                {"video_stem": stem, "filename": clips[1].filename},
            ],
            "transition": "cut",
            "title": "deltest",
        })
        comp_id = resp.json()["compilation_id"]
        _wait_for_compilation(app_client)

        # Delete it
        del_resp = app_client.delete(f"/api/compilation/{comp_id}")
        assert del_resp.status_code == 200

        # Verify it's gone
        list_resp = app_client.get("/api/compilations")
        ids = [c["compilation_id"] for c in list_resp.json()["compilations"]]
        assert comp_id not in ids

    def test_delete_nonexistent_compilation(self, output_dir, app_client):
        resp = app_client.delete("/api/compilation/comp_nonexistent")
        assert resp.status_code == 404
