"""Unit tests for clipper utility functions."""
from clipcutter.clipper import ensure_end_clip
from clipcutter.models import ClipBoundary, DetectionType


def _b(start: float, end: float) -> ClipBoundary:
    return ClipBoundary(start_time=start, end_time=end, highlights=[])


class TestEnsureEndClip:
    def test_adds_end_clip_when_uncovered(self):
        result = ensure_end_clip([_b(10.0, 50.0)], video_duration=600.0)
        assert len(result) == 2
        last = result[-1]
        assert last.end_time == 600.0
        assert last.start_time == 540.0  # 600 - 60

    def test_skips_when_boundary_reaches_end(self):
        result = ensure_end_clip([_b(500.0, 600.0)], video_duration=600.0)
        assert len(result) == 1

    def test_skips_within_tolerance(self):
        # Ends 3s before video end — within 5s tolerance
        result = ensure_end_clip([_b(500.0, 597.0)], video_duration=600.0)
        assert len(result) == 1

    def test_adds_when_just_outside_tolerance(self):
        # Ends 6s before video end — outside 5s tolerance
        result = ensure_end_clip([_b(500.0, 594.0)], video_duration=600.0)
        assert len(result) == 2

    def test_empty_boundaries_adds_clip(self):
        result = ensure_end_clip([], video_duration=600.0)
        assert len(result) == 1
        assert result[0].end_time == 600.0
        assert result[0].start_time == 540.0

    def test_short_video_clamped_to_start(self):
        # Video is 30s — shorter than END_CLIP_DURATION_SECONDS (60s)
        result = ensure_end_clip([], video_duration=30.0)
        assert len(result) == 1
        assert result[0].start_time == 0.0
        assert result[0].end_time == 30.0

    def test_added_clip_has_fallback_detection_type(self):
        result = ensure_end_clip([], video_duration=600.0)
        h = result[0].highlights[0]
        assert h.detection_type == DetectionType.FALLBACK

    def test_does_not_mutate_input_list(self):
        original = [_b(10.0, 50.0)]
        result = ensure_end_clip(original, video_duration=600.0)
        assert len(original) == 1  # input unchanged
        assert len(result) == 2
