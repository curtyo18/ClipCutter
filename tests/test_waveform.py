"""Tests for the waveform API endpoint."""

from tests.conftest import create_pending_clip, save_test_metadata


class TestWaveformEndpoint:
    """GET /api/waveform/{video_stem}/{filename} returns waveform data."""

    def test_waveform_returns_data(self, output_dir, app_client):
        stem = "wftest"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/wftest.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/wftest.mp4")

        resp = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        assert resp.status_code == 200

        data = resp.json()
        assert "waveform" in data
        assert "duration" in data
        assert "sample_count" in data
        assert isinstance(data["waveform"], list)
        assert len(data["waveform"]) > 0
        assert data["duration"] > 0

    def test_waveform_values_normalized(self, output_dir, app_client):
        stem = "wfnorm"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/wfnorm.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/wfnorm.mp4")

        resp = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        data = resp.json()

        for val in data["waveform"]:
            assert 0.0 <= val <= 1.0, f"Waveform value {val} out of [0,1] range"

    def test_waveform_caches_sidecar(self, output_dir, app_client):
        stem = "wfcache"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/wfcache.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/wfcache.mp4")

        # First request generates cache
        resp1 = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        assert resp1.status_code == 200

        # Check sidecar file was created
        clip_path = output_dir / "clips" / "pending" / stem / "clip_001.mp4"
        cache_path = clip_path.with_suffix(".mp4.waveform.json")
        assert cache_path.exists(), "Waveform cache sidecar should exist"

        # Second request uses cache
        resp2 = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        assert resp2.status_code == 200
        assert resp2.json()["waveform"] == resp1.json()["waveform"]

    def test_waveform_includes_highlight_regions(self, output_dir, app_client):
        stem = "wfregions"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/wfregions.mp4",
        )
        # Manually add highlight_regions to the clip metadata
        clip.highlight_regions = [
            {"offset": 0.5, "duration": 1.0, "type": "volume_spike", "confidence": 0.85}
        ]
        save_test_metadata(output_dir, stem, [clip], "/fake/wfregions.mp4")

        resp = app_client.get(f"/api/waveform/{stem}/clip_001.mp4")
        data = resp.json()
        assert "highlight_regions" in data
        assert len(data["highlight_regions"]) == 1
        assert data["highlight_regions"][0]["type"] == "volume_spike"

    def test_waveform_404_for_missing_clip(self, output_dir, app_client):
        resp = app_client.get("/api/waveform/noexist/fake.mp4")
        assert resp.status_code == 404


class TestClipsEndpointHighlightRegions:
    """GET /api/clips returns highlight_regions in clip data."""

    def test_clips_include_highlight_regions(self, output_dir, app_client):
        stem = "hrtest"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/hrtest.mp4",
        )
        clip.highlight_regions = [
            {"offset": 1.0, "duration": 2.0, "type": "laughter", "confidence": 0.7}
        ]
        save_test_metadata(output_dir, stem, [clip], "/fake/hrtest.mp4")

        resp = app_client.get("/api/clips")
        data = resp.json()
        assert data["total"] == 1
        assert len(data["clips"][0]["highlight_regions"]) == 1
