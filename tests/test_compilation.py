"""Tests for the compilation builder API."""

import json
import time
from pathlib import Path

from tests.conftest import create_pending_clip, save_test_metadata, keep_and_wait


def _keep_clip(app_client, stem, filename):
    """Helper: keep a pending clip so it's available for compilation."""
    keep_and_wait(app_client, stem, filename,
                  json_body={"trim_start": 0.0, "trim_end": 0.0})


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

    def test_list_compilations_includes_clip_count(self, output_dir, app_client):
        stem = "compcount"
        clips = _setup_kept_clips(output_dir, app_client, stem, count=2)

        app_client.post("/api/compilation", json={
            "clips": [
                {"video_stem": stem, "filename": clips[0].filename},
                {"video_stem": stem, "filename": clips[1].filename},
            ],
            "transition": "cut",
            "title": "counttest",
        })
        _wait_for_compilation(app_client)

        resp = app_client.get("/api/compilations")
        assert resp.status_code == 200
        for entry in resp.json()["compilations"]:
            assert isinstance(entry["clip_count"], int)
            assert entry["clip_count"] == len(entry["clips"])

    def test_list_compilations_backfills_missing_clip_count(
        self, output_dir, app_client
    ):
        meta_dir = output_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        # Use the canonical comp_YYYYMMDD_HHMMSS name so the tightened
        # list_compilations glob matches. The "legacy" aspect under test is
        # the absent clip_count field (older writers didn't emit it), not
        # a non-standard filename.
        comp_id = "comp_20260101_000000"
        legacy = {
            "compilation_id": comp_id,
            "filename": f"{comp_id}.mp4",
            "created_at": "2026-01-01T00:00:00",
            "clips": [
                {"video_stem": "legacy", "filename": "a.mp4", "duration": 1.0},
                {"video_stem": "legacy", "filename": "b.mp4", "duration": 1.0},
                {"video_stem": "legacy", "filename": "c.mp4", "duration": 1.0},
            ],
            "transition": "cut",
            "total_duration": 3.0,
            "status": "complete",
        }
        (meta_dir / f"{comp_id}.json").write_text(json.dumps(legacy), encoding="utf-8")

        resp = app_client.get("/api/compilations")
        assert resp.status_code == 200
        legacy_entry = next(
            c for c in resp.json()["compilations"]
            if c["compilation_id"] == comp_id
        )
        assert legacy_entry["clip_count"] == 3


class TestCompilationServe:
    """GET /video/compilation/{filename} serves compilation video files."""

    def test_serve_compilation_route_not_shadowed(self, output_dir, app_client):
        comp_dir = output_dir / "clips" / "compilations"
        comp_dir.mkdir(parents=True, exist_ok=True)
        comp_path = comp_dir / "foo.mp4"
        body = b"fake-mp4-bytes"
        comp_path.write_bytes(body)

        resp = app_client.get("/video/compilation/foo.mp4")
        assert resp.status_code == 200
        assert resp.content == body


class TestCompilationListGlobSpecificity:
    """list_compilations should only match comp_YYYYMMDD_HHMMSS.json,
    not arbitrary comp_*.json files like per-video clip metadata for
    a video stem starting with `comp_`."""

    def test_does_not_match_video_clip_metadata_named_like_compilation(
        self, output_dir, app_client
    ):
        """A video file named `comp_2024_finale.mp4` produces a metadata
        file `comp_2024_finale_clips.json` — which used to match the loose
        `comp_*.json` glob and surface as a (broken) "compilation"."""
        meta_dir = output_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)

        # Lookalike clip metadata: a real video stem that just happens
        # to start with "comp_". This is a clips file, not a compilation.
        (meta_dir / "comp_2024_finale_clips.json").write_text(
            json.dumps({
                "source_video": "/videos/comp_2024_finale.mp4",
                "processed_at": "2026-01-01T00:00:00",
                "clip_count": 1,
                "clips": [{
                    "filename": "clip_001.mp4",
                    "source_video": "/videos/comp_2024_finale.mp4",
                    "start_time": 0.0, "end_time": 10.0, "duration": 10.0,
                    "detection_reasons": ["volume_spike"], "confidence": 0.8,
                    "status": "kept",
                }],
            }),
            encoding="utf-8",
        )

        # And a real compilation file alongside it.
        comp_id = "comp_20260102_120000"
        (meta_dir / f"{comp_id}.json").write_text(
            json.dumps({
                "compilation_id": comp_id,
                "filename": f"{comp_id}.mp4",
                "created_at": "2026-01-02T12:00:00",
                "clips": [],
                "transition": "cut",
                "total_duration": 0.0,
                "status": "complete",
            }),
            encoding="utf-8",
        )

        resp = app_client.get("/api/compilations")
        assert resp.status_code == 200
        ids = [c.get("compilation_id") for c in resp.json()["compilations"]]
        assert comp_id in ids, (
            f"Real compilation should appear in list. Got: {ids!r}"
        )
        # The lookalike must NOT appear:
        assert not any(
            (i or "").startswith("comp_2024_finale") for i in ids
        ), f"Clip metadata for comp_2024_finale must NOT be listed. Got: {ids!r}"


class TestDeleteCompilationLeftoverFiles:
    """delete_compilation surfaces leftover_files when the .mp4 can't be
    removed, instead of swallowing the OSError and lying about success."""

    @staticmethod
    def _setup(output_dir, comp_id="comp_20260103_090000"):
        meta_dir = output_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        comp_dir = output_dir / "clips" / "compilations"
        comp_dir.mkdir(parents=True, exist_ok=True)

        video_filename = f"{comp_id}.mp4"
        (comp_dir / video_filename).write_bytes(b"fake-comp-bytes")
        (meta_dir / f"{comp_id}.json").write_text(
            json.dumps({
                "compilation_id": comp_id,
                "filename": video_filename,
                "created_at": "2026-01-03T09:00:00",
                "clips": [], "transition": "cut",
                "total_duration": 0.0, "status": "complete",
            }),
            encoding="utf-8",
        )
        return comp_id, video_filename

    def test_clean_delete_returns_empty_leftover_files(self, output_dir, app_client):
        comp_id, video_filename = self._setup(output_dir)
        resp = app_client.delete(f"/api/compilation/{comp_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["leftover_files"] == []
        # Both files actually gone
        assert not (output_dir / "metadata" / f"{comp_id}.json").exists()
        assert not (output_dir / "clips" / "compilations" / video_filename).exists()

    def test_failed_video_unlink_reports_leftover(
        self, output_dir, app_client, monkeypatch
    ):
        """Patch Path.unlink for the comp .mp4 to raise OSError. Response
        should still be 200 but leftover_files should contain the .mp4."""
        from pathlib import Path as _Path

        comp_id, video_filename = self._setup(output_dir)
        comp_mp4 = output_dir / "clips" / "compilations" / video_filename
        meta_json = output_dir / "metadata" / f"{comp_id}.json"

        original_unlink = _Path.unlink

        def fake_unlink(self_path, *args, **kwargs):
            # Refuse to delete just the .mp4 — let the metadata .json unlink
            # succeed so the rest of the cleanup happens.
            if self_path == comp_mp4:
                raise OSError("simulated unlink failure")
            return original_unlink(self_path, *args, **kwargs)

        monkeypatch.setattr(_Path, "unlink", fake_unlink)

        resp = app_client.delete(f"/api/compilation/{comp_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        # The mp4 path is reported relative to output_dir.
        assert data["leftover_files"], (
            "leftover_files must be non-empty when the .mp4 unlink fails"
        )
        # The path should be relative to output_dir, e.g.
        # "clips/compilations/comp_20260103_090000.mp4"
        leftover = data["leftover_files"][0]
        assert video_filename in leftover, (
            f"Leftover entry should reference the .mp4 by name. Got: {leftover!r}"
        )

        # The metadata json IS gone (no longer claiming the file exists).
        assert not meta_json.exists()
        # The .mp4 is still on disk because unlink was refused.
        assert comp_mp4.exists()


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


class TestCompilationSources:
    """DELETE /api/compilation/{id}/sources removes the source clip files."""

    @staticmethod
    def _write_compilation_meta(output_dir: Path, comp_id: str, clips: list) -> None:
        """Write a minimal compilation metadata JSON without running FFmpeg."""
        meta_dir = output_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "compilation_id": comp_id,
            "filename": f"{comp_id}.mp4",
            "created_at": "2026-01-01T00:00:00",
            "clips": clips,
            "transition": "cut",
            "total_duration": 2.0,
            "status": "complete",
        }
        (meta_dir / f"{comp_id}.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def test_delete_sources_removes_kept_files(self, output_dir, app_client):
        stem = "delsrc"
        clips_data = []
        for i in range(1, 3):
            fname = f"clip_{i:03d}.mp4"
            clip = create_pending_clip(
                output_dir, stem, fname,
                source_video=f"/fake/{stem}.mp4",
            )
            clips_data.append(clip)
        save_test_metadata(output_dir, stem, clips_data, f"/fake/{stem}.mp4")

        for clip in clips_data:
            _keep_clip(app_client, stem, clip.filename)

        # Both kept files should exist before we call delete
        for clip in clips_data:
            assert (output_dir / "clips" / "kept" / stem / clip.filename).exists()

        comp_id = "comp_srctest"
        self._write_compilation_meta(output_dir, comp_id, [
            {"video_stem": stem, "filename": clip.filename, "duration": 1.0}
            for clip in clips_data
        ])

        resp = app_client.delete(f"/api/compilation/{comp_id}/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_count"] == 2
        assert set(data["deleted"]) == {"clip_001.mp4", "clip_002.mp4"}

        # Kept files should be gone
        for clip in clips_data:
            assert not (output_dir / "clips" / "kept" / stem / clip.filename).exists()

        # Metadata should mark clips as deleted
        meta = json.loads(
            (output_dir / "metadata" / f"{stem}_clips.json").read_text(encoding="utf-8")
        )
        for c in meta["clips"]:
            assert c["status"] == "deleted", f"Expected deleted, got {c['status']}"

    def test_delete_sources_404_for_missing_compilation(self, output_dir, app_client):
        resp = app_client.delete("/api/compilation/comp_doesnotexist/sources")
        assert resp.status_code == 404
