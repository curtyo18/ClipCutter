"""Tests for metadata persistence: roundtrip, status updates, encoding info."""

import json
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from clipcutter.models import ClipMetadata
from clipcutter.metadata import (
    load_metadata,
    load_metadata_dict,
    save_metadata,
    update_clip_custom_name,
    update_clip_duration,
    update_clip_encoding,
    update_clip_status,
)


@pytest.fixture
def meta_dir():
    """Temp directory for metadata tests, cleaned up after each test."""
    d = Path(tempfile.mkdtemp(prefix="clipcutter_test_meta_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestMetadataRoundtrip:
    """Save and reload metadata, verify all fields survive."""

    def test_save_and_load(self, meta_dir):
        clips = [
            ClipMetadata(
                filename="clip_001.mp4",
                source_video="/videos/test.mp4",
                start_time=10.0,
                end_time=40.0,
                duration=30.0,
                detection_reasons=["volume_spike", "shouting"],
                confidence=0.85,
            ),
            ClipMetadata(
                filename="clip_002.mp4",
                source_video="/videos/test.mp4",
                start_time=60.0,
                end_time=90.0,
                duration=30.0,
                detection_reasons=["sudden_noise"],
                confidence=0.65,
            ),
        ]

        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)
        assert meta_path.exists()

        # Reload and verify
        loaded = load_metadata(meta_path)
        assert len(loaded) == 2
        assert loaded[0].filename == "clip_001.mp4"
        assert loaded[0].confidence == 0.85
        assert loaded[0].detection_reasons == ["volume_spike", "shouting"]
        assert loaded[1].filename == "clip_002.mp4"

        # Verify dict form
        raw = load_metadata_dict(meta_path)
        assert raw["source_video"] == "/videos/test.mp4"
        assert raw["clip_count"] == 2


class TestStatusUpdate:
    """update_clip_status persists correctly."""

    def test_update_status(self, meta_dir):
        clips = [
            ClipMetadata(
                filename="clip_001.mp4",
                source_video="/videos/test.mp4",
                start_time=0.0, end_time=10.0, duration=10.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            ),
        ]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        update_clip_status(meta_path, "clip_001.mp4", "kept")

        loaded = load_metadata(meta_path)
        assert loaded[0].status == "kept"


class TestCustomNamePersistence:
    """custom_name roundtrip through metadata."""

    def test_custom_name_roundtrip(self, meta_dir):
        clips = [
            ClipMetadata(
                filename="clip_001.mp4",
                source_video="/videos/test.mp4",
                start_time=0.0, end_time=10.0, duration=10.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            ),
        ]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        update_clip_custom_name(meta_path, "clip_001.mp4", "My Highlight")

        loaded = load_metadata(meta_path)
        assert loaded[0].custom_name == "My Highlight"

        # Verify raw JSON has the field
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        assert raw["clips"][0]["custom_name"] == "My Highlight"


class TestEncodingInfoPersistence:
    """Encoding filename and preset persist through metadata."""

    def test_encoding_info_roundtrip(self, meta_dir):
        clips = [
            ClipMetadata(
                filename="clip_001.mp4",
                source_video="/videos/test.mp4",
                start_time=0.0, end_time=10.0, duration=10.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            ),
        ]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        update_clip_encoding(meta_path, "clip_001.mp4",
                             "clip_001.high.mp4", "high")

        loaded = load_metadata(meta_path)
        assert loaded[0].encoded_filename == "clip_001.high.mp4"
        assert loaded[0].encoding_preset == "high"


class TestOptionalFieldsOmitted:
    """Optional None fields should not appear in serialized JSON."""

    def test_none_fields_omitted(self, meta_dir):
        clips = [
            ClipMetadata(
                filename="clip_001.mp4",
                source_video="/videos/test.mp4",
                start_time=0.0, end_time=10.0, duration=10.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            ),
        ]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        clip_dict = raw["clips"][0]

        # These should NOT be present when None
        for optional_key in ("custom_name", "encoded_filename",
                             "encoding_preset", "youtube_video_id",
                             "youtube_url", "youtube_upload_status"):
            assert optional_key not in clip_dict, (
                f"Optional field '{optional_key}' should not be serialized when None"
            )

        # highlight_regions now defaults to [], not None. Empty lists are
        # also omitted from serialized output to keep the JSON tight
        # (matches the pre-existing behavior of omitting falsy values).
        assert "highlight_regions" not in clip_dict, (
            "Empty highlight_regions list should not appear in serialized JSON"
        )


class TestHighlightRegionsDefault:
    """highlight_regions defaults to [] (not None) and from_dict coerces
    a null/missing value to [] for backward compat with older metadata
    files written when the field was Optional[List[dict]] = None."""

    def test_default_is_empty_list_not_none(self):
        clip = ClipMetadata(
            filename="clip_001.mp4",
            source_video="/videos/test.mp4",
            start_time=0.0, end_time=10.0, duration=10.0,
            detection_reasons=["volume_spike"], confidence=0.8,
        )
        assert clip.highlight_regions == []
        # Iteration is safe without a None check (the whole point):
        for _ in clip.highlight_regions:
            pass

    def test_from_dict_coerces_null_to_empty_list(self):
        """Older metadata files have `"highlight_regions": null` in the JSON.
        load_metadata must coerce that to [] so call sites can iterate."""
        legacy = {
            "filename": "clip_001.mp4",
            "source_video": "/videos/test.mp4",
            "start_time": 0.0, "end_time": 10.0, "duration": 10.0,
            "detection_reasons": ["volume_spike"], "confidence": 0.8,
            "highlight_regions": None,
        }
        clip = ClipMetadata.from_dict(legacy)
        assert clip.highlight_regions == []

    def test_from_dict_handles_missing_key(self):
        """Even-older metadata files predate the field. Must default to []."""
        legacy = {
            "filename": "clip_001.mp4",
            "source_video": "/videos/test.mp4",
            "start_time": 0.0, "end_time": 10.0, "duration": 10.0,
            "detection_reasons": ["volume_spike"], "confidence": 0.8,
        }
        clip = ClipMetadata.from_dict(legacy)
        assert clip.highlight_regions == []

    def test_from_dict_preserves_populated_list(self):
        """Sanity: a real highlight_regions list survives roundtripping."""
        regions = [
            {"offset": 1.0, "duration": 0.5, "type": "volume_spike", "confidence": 0.9},
        ]
        data = {
            "filename": "clip_001.mp4",
            "source_video": "/videos/test.mp4",
            "start_time": 0.0, "end_time": 10.0, "duration": 10.0,
            "detection_reasons": ["volume_spike"], "confidence": 0.8,
            "highlight_regions": regions,
        }
        clip = ClipMetadata.from_dict(data)
        assert clip.highlight_regions == regions

    def test_populated_regions_serialized(self, meta_dir):
        """A non-empty highlight_regions list IS serialized (the omit-when-empty
        rule mirrors the None-omit behavior of the other optional fields)."""
        clip = ClipMetadata(
            filename="clip_001.mp4",
            source_video="/videos/test.mp4",
            start_time=0.0, end_time=10.0, duration=10.0,
            detection_reasons=["volume_spike"], confidence=0.8,
            highlight_regions=[
                {"offset": 0.5, "duration": 1.0, "type": "laughter", "confidence": 0.7},
            ],
        )
        meta_path = save_metadata([clip], "/videos/test.mp4", meta_dir)
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        assert raw["clips"][0]["highlight_regions"] == [
            {"offset": 0.5, "duration": 1.0, "type": "laughter", "confidence": 0.7},
        ]


class TestDurationUpdate:
    """update_clip_duration persists the new duration correctly."""

    def test_update_duration(self, meta_dir):
        clips = [
            ClipMetadata(
                filename="clip_001.mp4",
                source_video="/videos/test.mp4",
                start_time=0.0, end_time=30.0, duration=30.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            ),
        ]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        update_clip_duration(meta_path, "clip_001.mp4", 20.0)

        loaded = load_metadata(meta_path)
        assert loaded[0].duration == 20.0

    def test_update_duration_in_raw_json(self, meta_dir):
        clips = [
            ClipMetadata(
                filename="clip_001.mp4",
                source_video="/videos/test.mp4",
                start_time=0.0, end_time=30.0, duration=30.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            ),
        ]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        update_clip_duration(meta_path, "clip_001.mp4", 15.5)

        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        assert raw["clips"][0]["duration"] == 15.5

    def test_update_duration_only_changes_target_clip(self, meta_dir):
        clips = [
            ClipMetadata(
                filename="clip_001.mp4",
                source_video="/videos/test.mp4",
                start_time=0.0, end_time=30.0, duration=30.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            ),
            ClipMetadata(
                filename="clip_002.mp4",
                source_video="/videos/test.mp4",
                start_time=60.0, end_time=90.0, duration=30.0,
                detection_reasons=["laughter"], confidence=0.7,
            ),
        ]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        update_clip_duration(meta_path, "clip_001.mp4", 20.0)

        loaded = load_metadata(meta_path)
        assert loaded[0].duration == 20.0
        assert loaded[1].duration == 30.0  # unchanged


class TestReturnValues:
    """update_clip_* return True when the filename matches, False otherwise."""

    def _meta_with_one(self, meta_dir):
        clips = [
            ClipMetadata(
                filename="clip_001.mp4",
                source_video="/videos/test.mp4",
                start_time=0.0, end_time=10.0, duration=10.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            ),
        ]
        return save_metadata(clips, "/videos/test.mp4", meta_dir)

    def test_status_match_returns_true(self, meta_dir):
        meta_path = self._meta_with_one(meta_dir)
        assert update_clip_status(meta_path, "clip_001.mp4", "kept") is True

    def test_status_miss_returns_false(self, meta_dir):
        meta_path = self._meta_with_one(meta_dir)
        assert update_clip_status(meta_path, "no_such_clip.mp4", "kept") is False

    def test_custom_name_miss_returns_false(self, meta_dir):
        meta_path = self._meta_with_one(meta_dir)
        assert update_clip_custom_name(meta_path, "ghost.mp4", "x") is False

    def test_encoding_miss_returns_false(self, meta_dir):
        meta_path = self._meta_with_one(meta_dir)
        assert update_clip_encoding(meta_path, "ghost.mp4", "out.mp4", "high") is False

    def test_duration_miss_returns_false(self, meta_dir):
        meta_path = self._meta_with_one(meta_dir)
        assert update_clip_duration(meta_path, "ghost.mp4", 5.0) is False


class TestConcurrentUpdates:
    """Per-path lock ensures every concurrent update_clip_* lands."""

    def test_concurrent_status_updates_all_persist(self, meta_dir):
        # Set up N clips
        N = 20
        clips = [
            ClipMetadata(
                filename=f"clip_{i:03d}.mp4",
                source_video="/videos/test.mp4",
                start_time=i * 10.0, end_time=(i + 1) * 10.0, duration=10.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            )
            for i in range(N)
        ]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        # Barrier so every thread starts the read-mutate-write at the same instant.
        start = threading.Barrier(N)
        errors: list[Exception] = []

        def worker(idx: int):
            start.wait()  # release all threads simultaneously
            try:
                ok = update_clip_status(
                    meta_path, f"clip_{idx:03d}.mp4", "kept"
                )
                if not ok:
                    errors.append(AssertionError(f"clip {idx} returned False"))
            except Exception as exc:  # noqa: BLE001 — surface anything thrown
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"thread errors: {errors!r}"

        # Every clip's status should now be "kept" — proves no writes were lost.
        loaded = load_metadata(meta_path)
        statuses = [c.status for c in loaded]
        assert statuses == ["kept"] * N, (
            f"lost updates detected — only {statuses.count('kept')} of {N} landed"
        )

    def test_concurrent_mixed_updates_all_persist(self, meta_dir):
        # Different update functions racing on the same metadata file
        clips = [
            ClipMetadata(
                filename=f"clip_{i:03d}.mp4",
                source_video="/videos/test.mp4",
                start_time=i * 10.0, end_time=(i + 1) * 10.0, duration=10.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            )
            for i in range(4)
        ]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        start = threading.Barrier(4)

        def t_status():
            start.wait()
            update_clip_status(meta_path, "clip_000.mp4", "kept")

        def t_custom():
            start.wait()
            update_clip_custom_name(meta_path, "clip_001.mp4", "Great Moment")

        def t_encoding():
            start.wait()
            update_clip_encoding(meta_path, "clip_002.mp4", "clip_002.high.mp4", "high")

        def t_duration():
            start.wait()
            update_clip_duration(meta_path, "clip_003.mp4", 7.5)

        threads = [
            threading.Thread(target=t_status),
            threading.Thread(target=t_custom),
            threading.Thread(target=t_encoding),
            threading.Thread(target=t_duration),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        loaded = load_metadata(meta_path)
        assert loaded[0].status == "kept"
        assert loaded[1].custom_name == "Great Moment"
        assert loaded[2].encoded_filename == "clip_002.high.mp4"
        assert loaded[2].encoding_preset == "high"
        assert loaded[3].duration == 7.5


class TestReprocessMerge:
    """save_metadata merges with an existing file on re-process so that
    user-applied state (review/encode/upload) survives a fresh detection
    run instead of being silently overwritten."""

    def _make_clip(self, idx: int, **overrides) -> ClipMetadata:
        """Build a fresh-detection ClipMetadata; overrides apply user state."""
        defaults = dict(
            filename=f"clip_{idx:03d}.mp4",
            source_video="/videos/test.mp4",
            start_time=float(idx * 10),
            end_time=float(idx * 10 + 10),
            duration=10.0,
            detection_reasons=["volume_spike"],
            confidence=0.8,
        )
        defaults.update(overrides)
        return ClipMetadata(**defaults)

    def test_fresh_save_no_existing_file(self, meta_dir):
        """When no file exists, behavior is unchanged: write fresh."""
        clips = [self._make_clip(1), self._make_clip(2)]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        loaded = load_metadata(meta_path)
        assert len(loaded) == 2
        assert all(c.status == "pending" for c in loaded)

    def test_matching_filenames_preserve_status(self, meta_dir):
        """Re-process: existing kept/discarded clips keep their status."""
        clips_v1 = [self._make_clip(1), self._make_clip(2)]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)
        update_clip_status(meta_path, "clip_001.mp4", "kept")
        update_clip_status(meta_path, "clip_002.mp4", "discarded")

        # Fresh detection produces the same filenames with status=pending
        clips_v2 = [self._make_clip(1), self._make_clip(2)]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        loaded = load_metadata(meta_path)
        statuses = {c.filename: c.status for c in loaded}
        assert statuses == {"clip_001.mp4": "kept", "clip_002.mp4": "discarded"}

    def test_custom_name_preserved_across_reprocess(self, meta_dir):
        clips_v1 = [self._make_clip(1)]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)
        update_clip_custom_name(meta_path, "clip_001.mp4", "My Best Moment")

        clips_v2 = [self._make_clip(1)]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        loaded = load_metadata(meta_path)
        assert loaded[0].custom_name == "My Best Moment"

    def test_encoded_filename_and_preset_preserved(self, meta_dir):
        clips_v1 = [self._make_clip(1)]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)
        update_clip_encoding(meta_path, "clip_001.mp4",
                             "clip_001.high.mp4", "high")

        clips_v2 = [self._make_clip(1)]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        loaded = load_metadata(meta_path)
        assert loaded[0].encoded_filename == "clip_001.high.mp4"
        assert loaded[0].encoding_preset == "high"

    def test_youtube_state_preserved(self, meta_dir):
        """Critical: re-process must not cause silent re-upload to YouTube."""
        from clipcutter.metadata import update_clip_youtube

        clips_v1 = [self._make_clip(1)]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)
        update_clip_youtube(meta_path, "clip_001.mp4",
                            "abc123XYZ", "https://youtu.be/abc123XYZ",
                            "uploaded")

        clips_v2 = [self._make_clip(1)]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        loaded = load_metadata(meta_path)
        assert loaded[0].youtube_video_id == "abc123XYZ"
        assert loaded[0].youtube_url == "https://youtu.be/abc123XYZ"
        assert loaded[0].youtube_upload_status == "uploaded"

    def test_highlight_regions_preserved(self, meta_dir):
        """Populated highlight_regions survive a re-process even if the
        fresh detection run produces no regions for that clip (or a
        different region set — the existing list wins)."""
        regions = [
            {"offset": 0.5, "duration": 1.0, "type": "laughter",
             "confidence": 0.7},
        ]
        clips_v1 = [self._make_clip(1, highlight_regions=regions)]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)
        update_clip_status(meta_path, "clip_001.mp4", "kept")

        # New detection has no highlight_regions for this clip
        clips_v2 = [self._make_clip(1)]  # default highlight_regions=[]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        loaded = load_metadata(meta_path)
        assert loaded[0].highlight_regions == regions

    def test_user_trimmed_duration_preserved(self, meta_dir):
        """A user-trimmed clip (status != pending, duration differs from
        new detection) keeps its trimmed duration on re-process."""
        clips_v1 = [self._make_clip(1, duration=10.0)]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)
        update_clip_status(meta_path, "clip_001.mp4", "kept")
        update_clip_duration(meta_path, "clip_001.mp4", 5.5)  # user trim

        # New detection re-computes duration as 10.0 — but user trim wins
        clips_v2 = [self._make_clip(1, duration=10.0)]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        loaded = load_metadata(meta_path)
        assert loaded[0].duration == 5.5

    def test_pending_clip_duration_not_preserved(self, meta_dir):
        """A pending clip (not reviewed) gets its duration from the
        fresh detection run — pending duration is not 'user-trimmed'."""
        clips_v1 = [self._make_clip(1, duration=10.0)]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)
        # leave status=pending; do NOT call update_clip_status

        # New detection produces a different duration
        clips_v2 = [self._make_clip(1, duration=12.5, end_time=22.5)]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        loaded = load_metadata(meta_path)
        assert loaded[0].duration == 12.5

    def test_kept_clip_with_matching_duration_uses_new_duration(self, meta_dir):
        """If the user hasn't actually changed the duration (old == new),
        the new value is used — no special 'preserve identical value' case."""
        clips_v1 = [self._make_clip(1, duration=10.0)]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)
        update_clip_status(meta_path, "clip_001.mp4", "kept")
        # User did NOT trim; durations remain identical (10.0)

        clips_v2 = [self._make_clip(1, duration=10.0)]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        loaded = load_metadata(meta_path)
        assert loaded[0].duration == 10.0
        assert loaded[0].status == "kept"

    def test_orphan_clip_preserved(self, meta_dir):
        """An old clip not in the new detection run survives the merge."""
        clips_v1 = [self._make_clip(1), self._make_clip(2), self._make_clip(3)]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)
        update_clip_status(meta_path, "clip_002.mp4", "kept")

        # New detection only finds clip_001 and clip_003; clip_002 is orphan
        clips_v2 = [self._make_clip(1), self._make_clip(3)]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        loaded = load_metadata(meta_path)
        names = [c.filename for c in loaded]
        assert "clip_002.mp4" in names
        kept = next(c for c in loaded if c.filename == "clip_002.mp4")
        assert kept.status == "kept"

    def test_partial_merge_mix_of_matching_new_and_orphan(self, meta_dir):
        """Combined scenario: matching clips merge, new clips are fresh,
        orphan clips survive — all in one re-process."""
        clips_v1 = [
            self._make_clip(1),  # will match in v2
            self._make_clip(2),  # will be orphan
        ]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)
        update_clip_status(meta_path, "clip_001.mp4", "kept")
        update_clip_custom_name(meta_path, "clip_001.mp4", "Saved")
        update_clip_status(meta_path, "clip_002.mp4", "discarded")

        clips_v2 = [
            self._make_clip(1),  # matches old
            self._make_clip(3),  # new
        ]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        loaded = load_metadata(meta_path)
        by_name = {c.filename: c for c in loaded}
        assert set(by_name.keys()) == {
            "clip_001.mp4", "clip_002.mp4", "clip_003.mp4",
        }
        # Matching: state preserved
        assert by_name["clip_001.mp4"].status == "kept"
        assert by_name["clip_001.mp4"].custom_name == "Saved"
        # Orphan: state preserved
        assert by_name["clip_002.mp4"].status == "discarded"
        # New: pending, no custom state
        assert by_name["clip_003.mp4"].status == "pending"
        assert by_name["clip_003.mp4"].custom_name is None

    def test_detection_fields_come_from_new_run(self, meta_dir):
        """Detection-derived fields (start_time, end_time,
        detection_reasons, confidence) are updated from the new run
        even when the filename matches an existing kept clip."""
        clips_v1 = [self._make_clip(
            1, start_time=10.0, end_time=20.0,
            detection_reasons=["volume_spike"], confidence=0.5,
        )]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)
        update_clip_status(meta_path, "clip_001.mp4", "kept")

        # Same filename, different detection (e.g. sensitivity changed)
        clips_v2 = [self._make_clip(
            1, start_time=12.0, end_time=24.0,
            detection_reasons=["laughter", "shouting"], confidence=0.92,
        )]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        loaded = load_metadata(meta_path)
        c = loaded[0]
        assert c.status == "kept"  # preserved
        assert c.start_time == 12.0  # from new run
        assert c.end_time == 24.0
        assert c.detection_reasons == ["laughter", "shouting"]
        assert c.confidence == 0.92

    def test_clip_count_reflects_merged_total(self, meta_dir):
        """Top-level clip_count is the merged total (new + orphans)."""
        clips_v1 = [self._make_clip(1), self._make_clip(2)]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)
        update_clip_status(meta_path, "clip_002.mp4", "kept")

        clips_v2 = [self._make_clip(1), self._make_clip(3)]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        raw = load_metadata_dict(meta_path)
        assert raw["clip_count"] == 3  # clip_001 (merged), clip_002 (orphan), clip_003 (new)

    def test_atomic_temp_then_rename(self, meta_dir, monkeypatch):
        """save_metadata writes to a .tmp file then renames atomically.
        Verify no .tmp file is left behind on success and that the rename
        path is exercised (no direct write to the final path)."""
        import clipcutter.metadata as md

        original_replace = Path.replace
        replaced: list[tuple[Path, Path]] = []

        def tracking_replace(self: Path, target):
            replaced.append((self, Path(target)))
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", tracking_replace)

        clips = [self._make_clip(1)]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        # Final file exists, no .tmp leftover
        assert meta_path.exists()
        assert not meta_path.with_suffix(".tmp").exists()
        # The rename was actually exercised: a .tmp → meta_path replace happened
        assert any(
            src.name.endswith(".tmp") and dst == meta_path
            for src, dst in replaced
        ), f"expected a .tmp -> {meta_path.name} rename, got {replaced!r}"

    def test_merge_path_also_atomic(self, meta_dir, monkeypatch):
        """The merge branch (existing file) also uses temp+rename."""
        clips_v1 = [self._make_clip(1)]
        meta_path = save_metadata(clips_v1, "/videos/test.mp4", meta_dir)

        # Track replace calls only for the second save
        original_replace = Path.replace
        replaced: list[tuple[Path, Path]] = []

        def tracking_replace(self: Path, target):
            replaced.append((self, Path(target)))
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", tracking_replace)

        clips_v2 = [self._make_clip(1), self._make_clip(2)]
        save_metadata(clips_v2, "/videos/test.mp4", meta_dir)

        assert not meta_path.with_suffix(".tmp").exists()
        assert any(
            src.name.endswith(".tmp") and dst == meta_path
            for src, dst in replaced
        )
