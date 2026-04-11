"""Tests for Export/Encoding API (Scenarios 2, 3)."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import create_pending_clip, create_pending_clip_long, save_test_metadata


class TestEncodingPresets:
    """Scenario 2: encode to each preset, verify output files."""

    @pytest.mark.parametrize("preset,expected_ext", [
        ("original", ".mp4"),  # Keeps source extension
        ("high", ".mp4"),
        ("low", ".mp4"),
        ("gif", ".gif"),
    ])
    def test_encode_preset(self, preset, expected_ext, output_dir, app_client):
        stem = f"enc_{preset}"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video=f"/fake/{stem}.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], f"/fake/{stem}.mp4")

        # Keep the clip first
        app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={"trim_start": 0.0, "trim_end": 0.0},
        )

        # Encode
        resp = app_client.post("/api/encode", json={
            "clips": [{"video_stem": stem, "filename": "clip_001.mp4"}],
            "preset": preset,
        })
        assert resp.status_code == 200

        _wait_encoding(app_client)

        # Verify encoded file exists
        encoded_dir = output_dir / "clips" / "encoded" / stem
        assert encoded_dir.exists(), f"Encoded dir should exist for preset '{preset}'"
        encoded_files = list(encoded_dir.iterdir())
        assert len(encoded_files) == 1, (
            f"Expected 1 encoded file for preset '{preset}', got {len(encoded_files)}"
        )
        assert encoded_files[0].suffix == expected_ext, (
            f"Expected extension {expected_ext}, got {encoded_files[0].suffix}"
        )

        # Verify metadata updated
        meta = _load_meta(output_dir, stem)
        clip_meta = meta["clips"][0]
        assert clip_meta["encoding_preset"] == preset
        assert clip_meta["encoded_filename"] is not None


class TestCustomNameInExport:
    """Scenario 3: custom_name used in encoded output filename."""

    def test_custom_name_in_output_filename(self, output_dir, app_client):
        stem = "namevid"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/namevid.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/namevid.mp4")

        # Keep with custom name
        app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={
                "custom_name": "My Great Moment",
                "trim_start": 0.0, "trim_end": 0.0,
            },
        )

        # Encode with copy preset
        resp = app_client.post("/api/encode", json={
            "clips": [{"video_stem": stem, "filename": "clip_001.mp4"}],
            "preset": "original",
        })
        assert resp.status_code == 200
        _wait_encoding(app_client)

        # Verify output uses sanitized custom name
        encoded_dir = output_dir / "clips" / "encoded" / stem
        encoded_files = list(encoded_dir.iterdir())
        assert len(encoded_files) == 1

        name = encoded_files[0].stem  # Without extension
        assert "My_Great_Moment" in name, (
            f"Expected 'My_Great_Moment' in filename, got '{name}'"
        )

    def test_encode_after_trim_uses_custom_name(self, output_dir, app_client):
        """Scenario 4 extended: trim + custom_name, then encode."""
        stem = "trimenc"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/trimenc.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/trimenc.mp4")

        # Keep with trim + custom name
        app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={
                "custom_name": "Trimmed",
                "trim_start": 0.0,
                "trim_end": 1.0,
            },
        )

        # Encode
        app_client.post("/api/encode", json={
            "clips": [{"video_stem": stem, "filename": "clip_001.mp4"}],
            "preset": "original",
        })
        _wait_encoding(app_client)

        # Verify custom name in output
        encoded_dir = output_dir / "clips" / "encoded" / stem
        encoded_files = list(encoded_dir.iterdir())
        assert len(encoded_files) == 1
        assert "Trimmed" in encoded_files[0].stem


class TestPresetsList:
    """Verify the presets endpoint returns expected presets."""

    def test_presets_endpoint(self, output_dir, app_client):
        resp = app_client.get("/api/encoding/presets")
        assert resp.status_code == 200
        data = resp.json()
        preset_names = {p["name"] for p in data["presets"]}
        assert preset_names == {"original", "high", "low", "gif"}
        assert data["default"] == "original"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_encoding(client, timeout: float = 60.0):
    """Poll /api/encode/status until encoding completes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get("/api/encode/status").json()
        if not status.get("running", False):
            errors = status.get("errors", [])
            if errors:
                pytest.fail(f"Encoding failed: {errors}")
            return
        time.sleep(0.3)
    pytest.fail("Encoding timed out")


def _load_meta(output_dir: Path, video_stem: str) -> dict:
    meta_path = output_dir / "metadata" / f"{video_stem}_clips.json"
    return json.loads(meta_path.read_text(encoding="utf-8"))


class TestKeptClipsResponse:
    """clipped_at field present in /api/kept response, sorted by date descending."""

    def test_clipped_at_included(self, output_dir, app_client):
        stem = "catvid"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/catvid.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/catvid.mp4",
                           processed_at="2026-03-15T10:00:00")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep",
                        json={"segments": []})

        resp = app_client.get("/api/kept")
        assert resp.status_code == 200
        clips = resp.json()["clips"]
        kept = next(c for c in clips if c["video_stem"] == stem)
        assert "clipped_at" in kept
        assert kept["clipped_at"] == "2026-03-15T10:00:00"

    def test_kept_clips_sorted_by_date_descending(self, output_dir, app_client):
        older_stem = "older_sort"
        newer_stem = "newer_sort"

        older_clip = create_pending_clip(
            output_dir, older_stem, "clip_001.mp4",
            source_video="/fake/older.mp4",
        )
        save_test_metadata(output_dir, older_stem, [older_clip], "/fake/older.mp4",
                           processed_at="2025-01-01T00:00:00")

        newer_clip = create_pending_clip(
            output_dir, newer_stem, "clip_001.mp4",
            source_video="/fake/newer.mp4",
        )
        save_test_metadata(output_dir, newer_stem, [newer_clip], "/fake/newer.mp4",
                           processed_at="2026-06-01T00:00:00")

        app_client.post(f"/api/clips/{older_stem}/clip_001.mp4/keep",
                        json={"segments": []})
        app_client.post(f"/api/clips/{newer_stem}/clip_001.mp4/keep",
                        json={"segments": []})

        resp = app_client.get("/api/kept")
        clips = resp.json()["clips"]
        test_clips = [c for c in clips
                      if c["video_stem"] in (older_stem, newer_stem)]
        assert len(test_clips) == 2
        assert test_clips[0]["video_stem"] == newer_stem
        assert test_clips[1]["video_stem"] == older_stem


class TestDeleteKeptClip:
    """DELETE /api/kept/{video_stem}/{filename} removes file and marks discarded."""

    def test_delete_removes_file(self, output_dir, app_client):
        stem = "delvid"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/delvid.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/delvid.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep",
                        json={"segments": []})

        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()

        resp = app_client.delete(f"/api/kept/{stem}/clip_001.mp4")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert not kept_path.exists()

    def test_delete_marks_metadata_discarded(self, output_dir, app_client):
        stem = "delmetavid"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/delmetavid.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/delmetavid.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep",
                        json={"segments": []})

        app_client.delete(f"/api/kept/{stem}/clip_001.mp4")

        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["status"] == "discarded"

    def test_delete_nonexistent_returns_404(self, output_dir, app_client):
        resp = app_client.delete("/api/kept/fakevid/nonexistent.mp4")
        assert resp.status_code == 404

    def test_deleted_clip_absent_from_kept_list(self, output_dir, app_client):
        stem = "delvid2"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/delvid2.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/delvid2.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep",
                        json={"segments": []})
        app_client.delete(f"/api/kept/{stem}/clip_001.mp4")

        resp = app_client.get("/api/kept")
        kept_for_stem = [c for c in resp.json()["clips"]
                         if c["video_stem"] == stem]
        assert len(kept_for_stem) == 0

    def test_delete_removes_empty_folder(self, output_dir, app_client):
        stem = "emptyfoldervid"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/emptyfoldervid.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/emptyfoldervid.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep",
                        json={"segments": []})

        kept_dir = output_dir / "clips" / "kept" / stem
        assert kept_dir.exists()

        app_client.delete(f"/api/kept/{stem}/clip_001.mp4")

        assert not kept_dir.exists(), "Empty kept folder should be removed after last clip deleted"


class TestOpenFolder:
    def test_open_folder_not_found_returns_404(self, output_dir, app_client):
        resp = app_client.get("/api/open-folder/kept/no_such_stem")
        assert resp.status_code == 404

    def test_open_folder_calls_startfile(self, output_dir, app_client):
        stem = "openfolder"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/openfolder.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/openfolder.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep",
                        json={"segments": []})

        with patch("clipcutter.routes.encode.os.startfile") as mock_startfile:
            resp = app_client.get(f"/api/open-folder/kept/{stem}")

        assert resp.status_code == 200
        assert resp.json()["status"] == "opened"
        mock_startfile.assert_called_once()
        called_path = mock_startfile.call_args[0][0]
        assert stem in called_path


class TestKeptClipSizes:
    """size_mb and encoded_size_mb fields present in /api/kept response."""

    def test_size_mb_present(self, output_dir, app_client):
        stem = "sizevid"
        clip = create_pending_clip_long(output_dir, stem, "clip_001.mp4",
                                        source_video="/fake/sizevid.mp4",
                                        file_duration_s=120.0)
        save_test_metadata(output_dir, stem, [clip], "/fake/sizevid.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})

        resp = app_client.get("/api/kept")
        assert resp.status_code == 200
        kept = next(c for c in resp.json()["clips"] if c["video_stem"] == stem)
        assert "size_mb" in kept
        assert kept["size_mb"] > 0

    def test_encoded_size_mb_null_when_not_encoded(self, output_dir, app_client):
        stem = "sizevid2"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/sizevid2.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/sizevid2.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})

        resp = app_client.get("/api/kept")
        kept = next(c for c in resp.json()["clips"] if c["video_stem"] == stem)
        assert kept["encoded_size_mb"] is None

    def test_encoded_size_mb_present_after_encode(self, output_dir, app_client):
        stem = "sizeenc"
        clip = create_pending_clip_long(output_dir, stem, "clip_001.mp4",
                                        source_video="/fake/sizeenc.mp4",
                                        file_duration_s=120.0)
        save_test_metadata(output_dir, stem, [clip], "/fake/sizeenc.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})
        app_client.post("/api/encode", json={
            "clips": [{"video_stem": stem, "filename": "clip_001.mp4"}],
            "preset": "original",
        })
        _wait_encoding(app_client)

        resp = app_client.get("/api/kept")
        kept = next(c for c in resp.json()["clips"] if c["video_stem"] == stem)
        assert kept["encoded_size_mb"] is not None
        assert kept["encoded_size_mb"] > 0


class TestDeleteEncodedClip:
    """DELETE /api/encoded/{stem}/{filename} removes encoded file and clears metadata."""

    def test_delete_encoded_removes_file(self, output_dir, app_client):
        stem = "delencvid"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/delencvid.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/delencvid.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})
        app_client.post("/api/encode", json={
            "clips": [{"video_stem": stem, "filename": "clip_001.mp4"}],
            "preset": "original",
        })
        _wait_encoding(app_client)

        # Confirm encoded file exists
        encoded_dir = output_dir / "clips" / "encoded" / stem
        assert encoded_dir.exists()
        encoded_files = list(encoded_dir.iterdir())
        assert len(encoded_files) == 1

        resp = app_client.delete(f"/api/encoded/{stem}/clip_001.mp4")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert resp.json()["freed_mb"] >= 0
        assert not any(encoded_dir.iterdir()) if encoded_dir.exists() else True

    def test_delete_encoded_clears_metadata(self, output_dir, app_client):
        stem = "delencmeta"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/delencmeta.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/delencmeta.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})
        app_client.post("/api/encode", json={
            "clips": [{"video_stem": stem, "filename": "clip_001.mp4"}],
            "preset": "original",
        })
        _wait_encoding(app_client)

        app_client.delete(f"/api/encoded/{stem}/clip_001.mp4")

        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["encoded_filename"] is None
        assert meta["clips"][0]["encoding_preset"] is None

    def test_delete_encoded_not_found_returns_404(self, output_dir, app_client):
        resp = app_client.delete("/api/encoded/fakevid/clip_001.mp4")
        assert resp.status_code == 404

    def test_kept_clip_untouched_after_delete_encoded(self, output_dir, app_client):
        stem = "delenckeep"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/delenckeep.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/delenckeep.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})
        app_client.post("/api/encode", json={
            "clips": [{"video_stem": stem, "filename": "clip_001.mp4"}],
            "preset": "original",
        })
        _wait_encoding(app_client)

        app_client.delete(f"/api/encoded/{stem}/clip_001.mp4")

        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists(), "Kept clip must still exist after deleting encoded version"


class TestStorageSummary:
    """GET /api/storage-summary returns counts and sizes for kept/encoded/compilations."""

    def test_empty_returns_zeros(self, output_dir, app_client):
        resp = app_client.get("/api/storage-summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kept"] == {"count": 0, "size_mb": 0.0}
        assert data["encoded"] == {"count": 0, "size_mb": 0.0}
        assert data["compilations"] == {"count": 0, "size_mb": 0.0}
        assert data["total_mb"] == 0.0

    def test_counts_kept_clips(self, output_dir, app_client):
        stem = "summary_test"
        clip = create_pending_clip_long(output_dir, stem, "clip_001.mp4",
                                        source_video="/fake/summary.mp4",
                                        file_duration_s=120.0)
        save_test_metadata(output_dir, stem, [clip], "/fake/summary.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})

        resp = app_client.get("/api/storage-summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kept"]["count"] == 1
        assert data["kept"]["size_mb"] > 0
        assert data["total_mb"] == data["kept"]["size_mb"]

    def test_total_sums_categories(self, output_dir, app_client):
        stem = "sumtotal"
        clip = create_pending_clip_long(output_dir, stem, "clip_001.mp4",
                                        source_video="/fake/sumtotal.mp4",
                                        file_duration_s=120.0)
        save_test_metadata(output_dir, stem, [clip], "/fake/sumtotal.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})

        resp = app_client.get("/api/storage-summary")
        data = resp.json()
        expected = round(
            data["kept"]["size_mb"] + data["encoded"]["size_mb"] + data["compilations"]["size_mb"],
            1,
        )
        assert data["total_mb"] == expected
        assert data["kept"]["size_mb"] > 0  # Ensure non-trivial state
