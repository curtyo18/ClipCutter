"""Tests for the Review API: keep/discard, custom names, trim (Scenarios 1, 4, 5)."""

import json
from pathlib import Path

from tests.conftest import create_pending_clip, save_test_metadata


class TestKeepClip:
    """Keeping a clip moves it to kept/ and updates metadata."""

    def test_keep_with_custom_name(self, output_dir, app_client):
        stem = "testvid"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/testvid.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/testvid.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={"custom_name": "test_clip", "trim_start": 0.0, "trim_end": 0.0},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "kept"

        # Verify file in kept directory
        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists(), "Kept clip file should exist"

        # Verify metadata updated
        meta = _load_meta(output_dir, stem)
        clip_meta = meta["clips"][0]
        assert clip_meta["status"] == "kept"
        assert clip_meta["custom_name"] == "test_clip"

    def test_keep_appears_in_kept_list(self, output_dir, app_client):
        stem = "testvid2"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/testvid2.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/testvid2.mp4")

        app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={"trim_start": 0.0, "trim_end": 0.0},
        )

        kept_resp = app_client.get("/api/kept")
        assert kept_resp.status_code == 200
        data = kept_resp.json()
        assert data["total"] >= 1
        filenames = [c["filename"] for c in data["clips"]]
        assert "clip_001.mp4" in filenames


class TestDiscardClip:
    """Discarding a clip updates metadata but doesn't move the file."""

    def test_discard_updates_metadata(self, output_dir, app_client):
        stem = "discardvid"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/discardvid.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/discardvid.mp4")

        resp = app_client.post(f"/api/clips/{stem}/clip_001.mp4/discard")
        assert resp.status_code == 200
        assert resp.json()["status"] == "discarded"

        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["status"] == "discarded"

    def test_discarded_not_in_pending_list(self, output_dir, app_client):
        stem = "discardvid2"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/discardvid2.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/discardvid2.mp4")

        app_client.post(f"/api/clips/{stem}/clip_001.mp4/discard")

        clips_resp = app_client.get("/api/clips")
        filenames = [c["filename"] for c in clips_resp.json()["clips"]]
        assert "clip_001.mp4" not in filenames, (
            "Discarded clip should not appear in pending list"
        )


class TestKeepDiscardMix:
    """Scenario 5: keep 1, discard 2, verify only kept clip remains in kept list."""

    def test_keep_one_discard_two(self, output_dir, app_client):
        stem = "mixvid"
        clips = [
            create_pending_clip(output_dir, stem, f"clip_{i:03d}.mp4",
                                source_video="/fake/mixvid.mp4",
                                confidence=0.9 - i * 0.1)
            for i in range(3)
        ]
        save_test_metadata(output_dir, stem, clips, "/fake/mixvid.mp4")

        # Keep first, discard others
        app_client.post(f"/api/clips/{stem}/clip_000.mp4/keep",
                        json={"trim_start": 0.0, "trim_end": 0.0})
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/discard")
        app_client.post(f"/api/clips/{stem}/clip_002.mp4/discard")

        # Pending list should be empty
        pending = app_client.get("/api/clips").json()
        pending_for_stem = [c for c in pending["clips"]
                           if c["video_stem"] == stem]
        assert len(pending_for_stem) == 0, "No pending clips should remain"

        # Kept list should have exactly 1
        kept = app_client.get("/api/kept").json()
        kept_for_stem = [c for c in kept["clips"]
                        if c["video_stem"] == stem]
        assert len(kept_for_stem) == 1
        assert kept_for_stem[0]["filename"] == "clip_000.mp4"


class TestTrimAndCustomName:
    """Scenario 4: keep with trim + custom name."""

    def test_trim_and_custom_name(self, output_dir, app_client):
        stem = "trimvid"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/trimvid.mp4",
            start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/trimvid.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={
                "trim_start": 0.0,
                "trim_end": 1.0,
                "needs_trim": True,
                "custom_name": "Trimmed",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "kept"
        assert data["trimmed"] is True

        # Verify kept file exists
        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()

        # Verify custom_name in metadata
        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["custom_name"] == "Trimmed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_meta(output_dir: Path, video_stem: str) -> dict:
    meta_path = output_dir / "metadata" / f"{video_stem}_clips.json"
    return json.loads(meta_path.read_text(encoding="utf-8"))
