"""Tests for Export/Encoding API (Scenarios 2, 3)."""

import json
import time
from pathlib import Path

import pytest

from tests.conftest import create_pending_clip, save_test_metadata


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
