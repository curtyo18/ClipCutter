"""Tests for Export/Encoding API (Scenarios 2, 3)."""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import create_pending_clip, create_pending_clip_long, save_test_metadata, keep_and_wait


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
        keep_and_wait(app_client, stem, "clip_001.mp4",
                      json_body={"trim_start": 0.0, "trim_end": 0.0})

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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={
            "custom_name": "My Great Moment",
            "trim_start": 0.0, "trim_end": 0.0,
        })

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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={
            "custom_name": "Trimmed",
            "trim_start": 0.0,
            "trim_end": 1.0,
        })

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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})

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

        keep_and_wait(app_client, older_stem, "clip_001.mp4", json_body={"segments": []})
        keep_and_wait(app_client, newer_stem, "clip_001.mp4", json_body={"segments": []})

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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})

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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})

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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})
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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})

        kept_dir = output_dir / "clips" / "kept" / stem
        assert kept_dir.exists()

        app_client.delete(f"/api/kept/{stem}/clip_001.mp4")

        assert not kept_dir.exists(), "Empty kept folder should be removed after last clip deleted"


class TestDeleteKeptClipLeftoverFiles:
    """delete_kept_clip surfaces leftover_files when the parent folder
    isn't empty after the unlink (e.g., a .waveform.json sidecar persists,
    or a sibling clip wasn't selected for deletion). Mirrors the shape
    used by delete_source so the UI can warn the user.

    Tests below write files directly (no ffmpeg) — the delete route only
    needs the file to exist, not to be a valid video.
    """

    def _setup_kept(self, output_dir: Path, stem: str, filename: str) -> Path:
        kept_dir = output_dir / "clips" / "kept" / stem
        kept_dir.mkdir(parents=True, exist_ok=True)
        kept_path = kept_dir / filename
        kept_path.write_bytes(b"fake-kept-bytes")

        # Minimal metadata so the delete route's update_clip_status finds
        # the entry it's looking for.
        meta_dir = output_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / f"{stem}_clips.json").write_text(json.dumps({
            "source_video": f"/fake/{stem}.mp4",
            "processed_at": "2026-01-01T00:00:00",
            "clip_count": 1,
            "clips": [{
                "filename": filename,
                "source_video": f"/fake/{stem}.mp4",
                "start_time": 0.0, "end_time": 10.0, "duration": 10.0,
                "detection_reasons": ["volume_spike"], "confidence": 0.8,
                "status": "kept",
            }],
        }), encoding="utf-8")
        return kept_path

    def test_clean_delete_returns_empty_leftover_files(self, output_dir, app_client):
        stem = "leftover_clean"
        kept_path = self._setup_kept(output_dir, stem, "clip_001.mp4")
        assert kept_path.exists()

        resp = app_client.delete(f"/api/kept/{stem}/clip_001.mp4")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["leftover_files"] == []
        # Parent folder removed when empty
        assert not kept_path.parent.exists()

    def test_waveform_sidecar_reported_as_leftover(self, output_dir, app_client):
        stem = "leftover_waveform"
        kept_path = self._setup_kept(output_dir, stem, "clip_001.mp4")
        # Drop a sidecar that the delete route doesn't sweep.
        sidecar = kept_path.with_suffix(".mp4.waveform.json")
        sidecar.write_text('{"waveform": [], "duration": 0}', encoding="utf-8")

        resp = app_client.delete(f"/api/kept/{stem}/clip_001.mp4")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        # leftover_files should mention the sidecar (path relative to
        # output_dir; matches delete_source's shape).
        assert data["leftover_files"], (
            "leftover_files must list the waveform sidecar"
        )
        # Each entry is a relative path string.
        assert any("waveform" in p for p in data["leftover_files"]), (
            f"Expected waveform sidecar in leftover_files. Got: {data['leftover_files']!r}"
        )
        # Sidecar still on disk; the kept clip itself is gone.
        assert sidecar.exists()
        assert not kept_path.exists()
        # Parent folder still exists because the sidecar is in it.
        assert kept_path.parent.exists()

    def test_sibling_clip_reported_as_leftover(self, output_dir, app_client):
        """Another kept clip in the same stem folder must be reported as
        leftover when only one is deleted — the folder cleanup is
        per-clip but the response should not lie about the folder state."""
        stem = "leftover_sibling"
        kept_a = self._setup_kept(output_dir, stem, "clip_a.mp4")
        kept_b_path = kept_a.parent / "clip_b.mp4"
        kept_b_path.write_bytes(b"fake-kept-bytes-b")

        resp = app_client.delete(f"/api/kept/{stem}/clip_a.mp4")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert any("clip_b" in p for p in data["leftover_files"]), (
            f"Sibling clip should appear in leftover_files. Got: {data['leftover_files']!r}"
        )


class TestOpenFolder:
    def test_open_folder_not_found_returns_404(self, output_dir, app_client):
        resp = app_client.get("/api/open-folder/kept/no_such_stem")
        assert resp.status_code == 404

    @pytest.mark.skipif(not hasattr(os, "startfile"), reason="Windows-only")
    def test_open_folder_calls_startfile(self, output_dir, app_client):
        stem = "openfolder"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/openfolder.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/openfolder.mp4")
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})

        with patch("clipcutter.routes.encode.os.startfile") as mock_startfile:
            resp = app_client.get(f"/api/open-folder/kept/{stem}")

        assert resp.status_code == 200
        assert resp.json()["status"] == "opened"
        mock_startfile.assert_called_once()
        called_path = mock_startfile.call_args[0][0]
        assert called_path == str(output_dir / "clips" / "kept" / stem)

    def test_open_folder_calls_xdg_open_on_linux(self, output_dir, app_client, monkeypatch):
        stem = "openfolder_linux"
        kept_dir = output_dir / "clips" / "kept" / stem
        kept_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr("clipcutter.routes.encode.sys.platform", "linux")
        with patch("clipcutter.routes.encode.subprocess.Popen") as mock_popen:
            resp = app_client.get(f"/api/open-folder/kept/{stem}")

        assert resp.status_code == 200
        assert resp.json()["status"] == "opened"
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        assert args[0] == ["xdg-open", str(kept_dir)]

    def test_open_folder_calls_open_on_macos(self, output_dir, app_client, monkeypatch):
        stem = "openfolder_mac"
        kept_dir = output_dir / "clips" / "kept" / stem
        kept_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr("clipcutter.routes.encode.sys.platform", "darwin")
        with patch("clipcutter.routes.encode.subprocess.Popen") as mock_popen:
            resp = app_client.get(f"/api/open-folder/kept/{stem}")

        assert resp.status_code == 200
        assert resp.json()["status"] == "opened"
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        assert args[0] == ["open", str(kept_dir)]


class TestKeptClipSizes:
    """size_mb and encoded_size_mb fields present in /api/kept response."""

    def test_size_mb_present(self, output_dir, app_client):
        stem = "sizevid"
        clip = create_pending_clip_long(output_dir, stem, "clip_001.mp4",
                                        source_video="/fake/sizevid.mp4",
                                        file_duration_s=120.0)
        save_test_metadata(output_dir, stem, [clip], "/fake/sizevid.mp4")
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})

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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})

        resp = app_client.get("/api/kept")
        kept = next(c for c in resp.json()["clips"] if c["video_stem"] == stem)
        assert kept["encoded_size_mb"] is None

    def test_encoded_size_mb_present_after_encode(self, output_dir, app_client):
        stem = "sizeenc"
        clip = create_pending_clip_long(output_dir, stem, "clip_001.mp4",
                                        source_video="/fake/sizeenc.mp4",
                                        file_duration_s=120.0)
        save_test_metadata(output_dir, stem, [clip], "/fake/sizeenc.mp4")
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})
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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})
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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})
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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})
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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})

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
        keep_and_wait(app_client, stem, "clip_001.mp4", json_body={"segments": []})

        resp = app_client.get("/api/storage-summary")
        data = resp.json()
        expected = round(
            data["kept"]["size_mb"] + data["encoded"]["size_mb"] + data["compilations"]["size_mb"],
            1,
        )
        assert data["total_mb"] == expected
        assert data["kept"]["size_mb"] > 0  # Ensure non-trivial state


class TestPathTraversalRejection:
    """Path components that contain `..` must be refused before any FS work.

    These exercise _safe_join applied to the routes that take untrusted
    video_stem / filename / compilation_id path parameters. We use `%2e%2e`
    (URL-encoded `..`) so the value survives client-side path normalization
    and reaches the handler as a literal `..` segment — the actually
    exploitable form under the real server (uvicorn decodes %2F to /, which
    would otherwise be re-segmented by the router into 404). We assert a
    400 response from the helper, and for delete endpoints that no file
    outside output_dir was touched.
    """

    def test_serve_video_rejects_dotdot_in_stem(self, output_dir, app_client):
        resp = app_client.get("/video/pending/%2e%2e/foo.mp4")
        assert resp.status_code == 400

    def test_serve_video_rejects_dotdot_in_filename(self, output_dir, app_client):
        resp = app_client.get("/video/pending/foo/%2e%2e")
        assert resp.status_code == 400

    def test_get_waveform_rejects_dotdot(self, output_dir, app_client):
        resp = app_client.get("/api/waveform/%2e%2e/foo.mp4")
        assert resp.status_code == 400

    def test_serve_encoded_rejects_dotdot(self, output_dir, app_client):
        resp = app_client.get("/video/encoded/%2e%2e/foo.mp4")
        assert resp.status_code == 400

    def test_serve_kept_rejects_dotdot(self, output_dir, app_client):
        resp = app_client.get("/video/kept/%2e%2e/foo.mp4")
        assert resp.status_code == 400

    def test_serve_video_rejects_unknown_kind(self, output_dir, app_client):
        """`kind` is constrained to {pending, kept, encoded}; anything else 400s.

        Validation of `kind` happens before _safe_join touches the filesystem,
        so a bogus kind always returns 400 even with otherwise-valid stem/file.
        """
        resp = app_client.get("/video/bogus/some_stem/foo.mp4")
        assert resp.status_code == 400

    def test_serve_compilation_rejects_dotdot(self, output_dir, app_client):
        resp = app_client.get("/video/compilation/%2e%2e")
        assert resp.status_code == 400

    def test_delete_kept_clip_rejects_dotdot_in_stem(self, output_dir, app_client):
        # Set up a real kept clip so a non-traversal request would succeed.
        kept_dir = output_dir / "clips" / "kept" / "real_stem"
        kept_dir.mkdir(parents=True, exist_ok=True)
        legit = kept_dir / "legit.mp4"
        legit.write_text("legit data", encoding="utf-8")

        # Traversal attempt: stem=".."
        resp = app_client.delete("/api/kept/%2e%2e/legit.mp4")
        assert resp.status_code == 400
        assert legit.exists(), (
            "Traversal must be rejected before any unlink. The legit clip "
            "must not have been touched."
        )


class TestServeVideoKind:
    """GET /video/{kind}/{video_stem}/{filename} — canonical shape.

    Replaces the older silent pending->kept->encoded fallback. Each kind
    serves only from its own directory; a clip that exists in a different
    kind is NOT a match (no fallback). Unknown kinds are rejected 400.
    """

    def _write_stub(self, path: Path, body: bytes = b"stub") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def test_serve_pending(self, output_dir, app_client):
        self._write_stub(output_dir / "clips" / "pending" / "stem" / "a.mp4", b"PENDING")
        resp = app_client.get("/video/pending/stem/a.mp4")
        assert resp.status_code == 200
        assert resp.content == b"PENDING"

    def test_serve_kept(self, output_dir, app_client):
        self._write_stub(output_dir / "clips" / "kept" / "stem" / "a.mp4", b"KEPT")
        resp = app_client.get("/video/kept/stem/a.mp4")
        assert resp.status_code == 200
        assert resp.content == b"KEPT"

    def test_serve_encoded(self, output_dir, app_client):
        self._write_stub(output_dir / "clips" / "encoded" / "stem" / "a.mp4", b"ENCODED")
        resp = app_client.get("/video/encoded/stem/a.mp4")
        assert resp.status_code == 200
        assert resp.content == b"ENCODED"

    def test_no_fallback_across_kinds(self, output_dir, app_client):
        """The original bug: a file existed in kept/ but not encoded/, and
        the old serve_video silently returned the kept version when asked
        for the encoded one. Now an encoded-kind request must 404 instead
        of falling back to the kept copy."""
        self._write_stub(output_dir / "clips" / "kept" / "stem" / "a.mp4", b"KEPT")
        # Encoded directory exists with no a.mp4; request explicitly asks
        # for encoded.
        (output_dir / "clips" / "encoded" / "stem").mkdir(parents=True, exist_ok=True)
        resp = app_client.get("/video/encoded/stem/a.mp4")
        assert resp.status_code == 404

    def test_unknown_kind_returns_400(self, output_dir, app_client):
        # Note: explicit kind validation runs before any FS check, so the
        # absence of the stem/file doesn't matter — only the bad kind.
        resp = app_client.get("/video/bogus/stem/a.mp4")
        assert resp.status_code == 400

    def test_compilation_kind_not_accepted_by_unified_route(
        self, output_dir, app_client,
    ):
        """`compilation` is served by its own /video/compilation/{filename}
        route (no stem). Asking the unified kind route for "compilation"
        must 400 — the unified route's whitelist does not include it."""
        resp = app_client.get("/video/compilation/stem/a.mp4")
        assert resp.status_code == 400

    def test_pending_404_when_missing(self, output_dir, app_client):
        resp = app_client.get("/video/pending/nonexistent/foo.mp4")
        assert resp.status_code == 404

    def test_kept_404_when_missing(self, output_dir, app_client):
        resp = app_client.get("/video/kept/nonexistent/foo.mp4")
        assert resp.status_code == 404

    def test_encoded_404_when_missing(self, output_dir, app_client):
        resp = app_client.get("/video/encoded/nonexistent/foo.mp4")
        assert resp.status_code == 404

    def test_legacy_no_kind_url_no_longer_routes(self, output_dir, app_client):
        """Hand-built /video/{stem}/{file} URLs from before the canonical
        rework no longer match a route — they get caught by either the
        compilation route (and 404 there) or simply fall through. The point
        is that they MUST NOT silently serve the file from pending/kept/encoded."""
        self._write_stub(output_dir / "clips" / "pending" / "stem" / "a.mp4", b"X")
        resp = app_client.get("/video/stem/a.mp4")
        assert resp.status_code in (400, 404)
        assert resp.content != b"X", (
            "Legacy 2-segment URL must not silently serve the pending file."
        )


class TestKeptClipVideoUrl:
    """The /api/kept response embeds video_url and (when encoded) encoded_video_url.
    Both must use the canonical /video/{kind}/{stem}/{file} shape.

    These tests construct kept/encoded directories and metadata directly
    (bypassing the keep/encode workers and their ffmpeg dependency) since
    we're only exercising the URL-builder field of the JSON response, not
    the actual trim or encode behaviour.
    """

    def _seed_kept(self, output_dir: Path, stem: str, filename: str,
                   *, encoded_filename: str | None = None,
                   encoded_preset: str | None = None) -> None:
        kept_dir = output_dir / "clips" / "kept" / stem
        kept_dir.mkdir(parents=True, exist_ok=True)
        (kept_dir / filename).write_bytes(b"kept-bytes")
        if encoded_filename:
            enc_dir = output_dir / "clips" / "encoded" / stem
            enc_dir.mkdir(parents=True, exist_ok=True)
            (enc_dir / encoded_filename).write_bytes(b"encoded-bytes")
        meta_dir = output_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        clip_entry = {
            "filename": filename,
            "source_video": f"/fake/{stem}.mp4",
            "start_time": 0.0,
            "end_time": 1.0,
            "duration": 1.0,
            "detection_reasons": ["volume_spike"],
            "confidence": 0.8,
            "status": "kept",
            "encoded_filename": encoded_filename,
            "encoding_preset": encoded_preset,
        }
        (meta_dir / f"{stem}_clips.json").write_text(json.dumps({
            "source_video": f"/fake/{stem}.mp4",
            "processed_at": "2026-01-01T00:00:00",
            "clip_count": 1,
            "clips": [clip_entry],
        }), encoding="utf-8")

    def test_kept_video_url_uses_kept_kind(self, output_dir, app_client):
        stem = "urltest"
        self._seed_kept(output_dir, stem, "clip_001.mp4")

        resp = app_client.get("/api/kept")
        assert resp.status_code == 200
        kept = resp.json()["clips"]
        assert len(kept) == 1
        assert kept[0]["video_url"] == f"/video/kept/{stem}/clip_001.mp4"
        # Not encoded -> no encoded_video_url field
        assert "encoded_video_url" not in kept[0]

    def test_encoded_video_url_uses_encoded_kind(self, output_dir, app_client):
        stem = "encurl"
        self._seed_kept(
            output_dir, stem, "clip_001.mp4",
            encoded_filename="clip_001.high.mp4", encoded_preset="high",
        )

        kept = app_client.get("/api/kept").json()["clips"]
        assert len(kept) == 1
        assert kept[0]["video_url"] == f"/video/kept/{stem}/clip_001.mp4"
        assert kept[0]["encoded_video_url"] == (
            f"/video/encoded/{stem}/clip_001.high.mp4"
        )
        # Sanity: the URL the backend constructed actually serves the file.
        served = app_client.get(kept[0]["encoded_video_url"])
        assert served.status_code == 200
        assert served.content == b"encoded-bytes"


class TestClipsVideoUrl:
    """The /api/clips response embeds video_url; must use kind=pending.

    Constructs the pending dir + metadata directly (no ffmpeg) because we
    only need to exercise the URL-builder, not the underlying clip data.
    """

    def test_pending_clips_video_url_uses_pending_kind(self, output_dir, app_client):
        stem = "pendurl"
        pending_dir = output_dir / "clips" / "pending" / stem
        pending_dir.mkdir(parents=True, exist_ok=True)
        (pending_dir / "clip_001.mp4").write_bytes(b"pending-bytes")
        meta_dir = output_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / f"{stem}_clips.json").write_text(json.dumps({
            "source_video": f"/fake/{stem}.mp4",
            "processed_at": "2026-01-01T00:00:00",
            "clip_count": 1,
            "clips": [{
                "filename": "clip_001.mp4",
                "source_video": f"/fake/{stem}.mp4",
                "start_time": 0.0,
                "end_time": 1.0,
                "duration": 1.0,
                "detection_reasons": ["volume_spike"],
                "confidence": 0.8,
                "status": "pending",
            }],
        }), encoding="utf-8")

        resp = app_client.get("/api/clips")
        assert resp.status_code == 200
        clips = resp.json()["clips"]
        assert len(clips) == 1
        assert clips[0]["video_url"] == f"/video/pending/{stem}/clip_001.mp4"
