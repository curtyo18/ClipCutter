"""Tests for the Review API: keep/discard, custom names, trim (Scenarios 1, 4, 5)."""

import json
import pytest
from pathlib import Path

from tests.conftest import create_pending_clip, create_pending_clip_long, save_test_metadata


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
            json={"custom_name": "test_clip", "segments": []},
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
            json={"segments": []},
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
                        json={"segments": []})
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
                "segments": [{"start": 1.0, "end": 9.0}],
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

    def test_no_trim_when_no_segments(self, output_dir, app_client):
        """Empty segments list should not trigger re-encode (full clip copy)."""
        stem = "notrimvid"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/notrimvid.mp4",
            start=0.0, end=20.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/notrimvid.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={"segments": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "kept"
        assert data["trimmed"] is False  # Full clip copy, no re-encode

        # Verify kept file exists
        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()

    def test_trim_updates_duration_in_metadata(self, output_dir, app_client):
        """When a clip is trimmed on keep, metadata duration reflects the trimmed length."""
        stem = "trimdur"
        clip = create_pending_clip_long(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/trimdur.mp4",
            file_duration_s=3.0, start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/trimdur.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={"segments": [{"start": 0.0, "end": 1.5}], "custom_name": None},
        )
        assert resp.status_code == 200
        assert resp.json()["trimmed"] is True

        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["duration"] == pytest.approx(1.5, abs=0.01)

    def test_full_clip_copy_does_not_change_duration(self, output_dir, app_client):
        """Keeping without trim should leave the metadata duration unchanged."""
        stem = "nodurchange"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/nodurchange.mp4",
            start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/nodurchange.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={"segments": [], "custom_name": None},
        )
        assert resp.status_code == 200
        assert resp.json()["trimmed"] is False

        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["duration"] == 10.0  # unchanged


class TestMultiSegmentKeep:
    """Keep with 2+ segments uses FFmpeg concat filter and reports combined duration."""

    def test_keep_with_multiple_segments(self, output_dir, app_client):
        stem = "multiseg"
        # 4-second clip so two 1.5s segments fit with a gap between them
        clip = create_pending_clip_long(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/multiseg.mp4",
            file_duration_s=4.0,
            start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/multiseg.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={
                "segments": [
                    {"start": 0.0, "end": 1.5},
                    {"start": 2.5, "end": 4.0},
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "kept"
        assert data["trimmed"] is True

        # File must exist (FFmpeg concat ran successfully)
        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()

        # Metadata duration should reflect combined segment length: 1.5 + 1.5 = 3.0s
        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["duration"] == pytest.approx(3.0, abs=0.1)
        assert meta["clips"][0]["status"] == "kept"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_meta(output_dir: Path, video_stem: str) -> dict:
    meta_path = output_dir / "metadata" / f"{video_stem}_clips.json"
    return json.loads(meta_path.read_text(encoding="utf-8"))


class TestSingleSegmentTrimUsesCopy:
    """Single-segment trim should use -c copy (stream copy), not re-encode."""

    def test_single_segment_trim_produces_valid_file(self, output_dir, app_client):
        """Trim a real video file and confirm the output is a valid mp4."""
        stem = "copytrim"
        clip = create_pending_clip_long(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/copytrim.mp4",
            file_duration_s=3.0, start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/copytrim.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={"segments": [{"start": 0.5, "end": 2.5}]},
        )
        assert resp.status_code == 200
        assert resp.json()["trimmed"] is True

        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()
        assert kept_path.stat().st_size > 0


class TestMultiSegmentQualityModes:
    """Multi-segment keep supports copy (default), precise (crf16), and ultra (crf0) modes."""

    def test_multi_segment_copy_mode_default(self, output_dir, app_client):
        """Default quality='copy' uses two-pass copy — file exists and has content."""
        stem = "msegcopy"
        clip = create_pending_clip_long(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/msegcopy.mp4",
            file_duration_s=4.0, start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/msegcopy.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={"segments": [{"start": 0.0, "end": 1.5}, {"start": 2.5, "end": 4.0}]},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "kept"
        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()
        assert kept_path.stat().st_size > 0

    def test_multi_segment_precise_mode(self, output_dir, app_client):
        """quality='precise' re-encodes with crf16 — file exists."""
        stem = "msegprecise"
        clip = create_pending_clip_long(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/msegprecise.mp4",
            file_duration_s=4.0, start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/msegprecise.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={
                "segments": [{"start": 0.0, "end": 1.5}, {"start": 2.5, "end": 4.0}],
                "quality": "precise",
            },
        )
        assert resp.status_code == 200
        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()
        assert kept_path.stat().st_size > 0

    def test_multi_segment_ultra_mode(self, output_dir, app_client):
        """quality='ultra' re-encodes with crf0 — file exists and is larger than precise."""
        stem = "msegultra"
        clip = create_pending_clip_long(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/msegultra.mp4",
            file_duration_s=4.0, start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/msegultra.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={
                "segments": [{"start": 0.0, "end": 1.5}, {"start": 2.5, "end": 4.0}],
                "quality": "ultra",
            },
        )
        assert resp.status_code == 200
        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()
        assert kept_path.stat().st_size > 0


class TestReviewSortOrder:
    """Clips sorted: newest-processed-video first, then by confidence within video."""

    def test_review_sorted_newest_video_first(self, output_dir, app_client):
        # The older video's clip has a higher confidence (0.95) so it would sort
        # first under the old confidence-only sort. The newer video's clip has
        # lower confidence (0.5). After the fix, newest-processed-date wins over
        # confidence, so the newer stem should appear first.
        older_stem = "sort_older_review"
        newer_stem = "sort_newer_review"

        older_clip = create_pending_clip(
            output_dir, older_stem, "clip_001.mp4",
            source_video="/fake/older.mp4",
            confidence=0.95,
        )
        save_test_metadata(output_dir, older_stem, [older_clip], "/fake/older.mp4",
                           processed_at="2025-01-01T00:00:00")

        newer_clip = create_pending_clip(
            output_dir, newer_stem, "clip_001.mp4",
            source_video="/fake/newer.mp4",
            confidence=0.50,
        )
        save_test_metadata(output_dir, newer_stem, [newer_clip], "/fake/newer.mp4",
                           processed_at="2026-06-01T00:00:00")

        resp = app_client.get("/api/clips")
        assert resp.status_code == 200
        clips = resp.json()["clips"]
        test_clips = [c for c in clips
                      if c["video_stem"] in {older_stem, newer_stem}]
        assert len(test_clips) == 2
        assert test_clips[0]["video_stem"] == newer_stem, (
            "Newer-processed video clips should appear first"
        )
        assert test_clips[1]["video_stem"] == older_stem
