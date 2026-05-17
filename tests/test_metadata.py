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
