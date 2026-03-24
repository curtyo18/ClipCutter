"""Tests for metadata persistence: roundtrip, status updates, encoding info."""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from clipcutter.models import ClipMetadata
from clipcutter.metadata import (
    load_metadata,
    load_metadata_dict,
    save_metadata,
    update_clip_custom_name,
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
