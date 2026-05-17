"""Unit tests for clipper utility functions."""
from clipcutter import config
from clipcutter.clipper import (
    MAX_SPLIT_DEPTH,
    _split_long_clip,
    compute_clip_boundaries,
    ensure_end_clip,
)
from clipcutter.models import ClipBoundary, DetectionType, Highlight


def _b(start: float, end: float) -> ClipBoundary:
    return ClipBoundary(start_time=start, end_time=end, highlights=[])


def _h(ts: float, dur: float = 1.0,
       dtype: DetectionType = DetectionType.VOLUME_SPIKE,
       confidence: float = 0.5) -> Highlight:
    return Highlight(
        timestamp=ts,
        duration=dur,
        detection_type=dtype,
        raw_score=1.0,
        confidence=confidence,
    )


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


class TestSplitLongClip:
    """Splitter must never produce a negative-duration boundary, even when
    several highlights pile up on the same timestamp (best_gap=0 path)."""

    def test_overlapping_highlights_produce_only_positive_durations(self):
        # Clip is 600s long — well over CLIP_MAX_LENGTH_SECONDS (240s).
        # All highlights are bunched within a 1s window, so the largest
        # inter-highlight gap is 0 and the pre-clamp split_point lands
        # ~at one of the highlight timestamps (nowhere near the clip middle).
        clip = ClipBoundary(
            start_time=0.0,
            end_time=600.0,
            highlights=[
                _h(300.0, dur=0.2, dtype=DetectionType.VOLUME_SPIKE),
                _h(300.1, dur=0.2, dtype=DetectionType.LAUGHTER),
                _h(300.2, dur=0.2, dtype=DetectionType.SHOUTING),
                _h(300.5, dur=0.2, dtype=DetectionType.SUDDEN_NOISE),
            ],
        )

        result = _split_long_clip(clip, video_duration=600.0)

        assert result, "splitter returned nothing"
        for sub in result:
            assert sub.duration > 0, f"got non-positive duration: {sub.duration}"
            assert sub.start_time >= clip.start_time
            assert sub.end_time <= clip.end_time

    def test_identical_timestamp_highlights(self):
        # Worst case: every highlight at literally the same timestamp.
        # best_gap is 0 and split_point collapses onto that one timestamp.
        clip = ClipBoundary(
            start_time=0.0,
            end_time=800.0,
            highlights=[_h(400.0, dur=0.1) for _ in range(5)],
        )

        result = _split_long_clip(clip, video_duration=800.0)

        assert result
        for sub in result:
            assert sub.duration > 0
            # And, defensively, no sub-boundary exceeds max length.
            assert sub.duration <= config.CLIP_MAX_LENGTH_SECONDS + 0.01

    def test_recursion_terminates_on_pathological_input(self):
        # If clamp + depth bound aren't enforced, this can blow the stack
        # or hang. Use a clip many multiples of CLIP_MAX with overlapping
        # highlights so best_gap=0 every time.
        clip = ClipBoundary(
            start_time=0.0,
            end_time=config.CLIP_MAX_LENGTH_SECONDS * 50,  # 12000s
            highlights=[
                _h(100.0, dur=0.1),
                _h(100.05, dur=0.1),
                _h(100.1, dur=0.1),
            ],
        )

        # Must not raise RecursionError. Bounded depth + halving guarantee
        # termination; we assert by simply returning at all.
        result = _split_long_clip(clip, video_duration=config.CLIP_MAX_LENGTH_SECONDS * 50)
        assert result
        for sub in result:
            assert sub.duration > 0

    def test_depth_fallback_uses_midpoint(self):
        # At depth >= MAX_SPLIT_DEPTH the splitter ignores the highlights
        # and halves the clip evenly. We can verify by calling with depth
        # exactly equal to the cap on a clip just over max length.
        clip = ClipBoundary(
            start_time=100.0,
            end_time=100.0 + config.CLIP_MAX_LENGTH_SECONDS * 2,  # 580.0
            highlights=[_h(150.0, dur=0.1), _h(150.1, dur=0.1)],
        )

        result = _split_long_clip(clip, video_duration=1000.0, depth=MAX_SPLIT_DEPTH)

        assert result
        # Even-halving puts the midpoint at start + (end-start)/2
        # = 100 + (480/2) = 340. Both halves should be 240s each.
        expected_mid = (clip.start_time + clip.end_time) / 2
        # The first sub-boundary should end at the midpoint (clamped).
        assert abs(result[0].end_time - expected_mid) < 0.01


class TestComputeClipBoundariesMerge:
    """Merging must isolate highlight ownership and only keep in-range
    highlights after merge."""

    def test_merged_highlights_stay_within_range(self):
        # Two highlights close enough to merge (within CLIP_MERGE_GAP plus
        # context padding on both sides). After merge, every highlight in
        # the returned boundary must have a timestamp in [start, end].
        highlights = [
            _h(50.0, dur=1.0),
            _h(70.0, dur=1.0),  # 20s after first; will merge given default config
        ]

        result = compute_clip_boundaries(highlights, video_duration=600.0)

        assert result
        for b in result:
            for h in b.highlights:
                assert b.start_time <= h.timestamp <= b.end_time, (
                    f"highlight at {h.timestamp} outside boundary "
                    f"[{b.start_time}, {b.end_time}]"
                )

    def test_merge_does_not_share_list_identity(self):
        # Two highlights close enough to merge.
        highlights = [_h(50.0, dur=1.0), _h(65.0, dur=1.0)]

        result = compute_clip_boundaries(highlights, video_duration=600.0)

        # After merge there should be exactly one boundary owning both
        # highlights — and the highlights list must be a fresh list, not
        # the original single-element list reused from the first boundary.
        # We verify by mutating it and confirming the highlights weren't
        # aliased back to anything we passed in.
        assert len(result) == 1
        original_count = len(result[0].highlights)
        result[0].highlights.append(_h(999.0, dur=0.1))
        # Mutation must not leak into the input highlights list.
        assert len(highlights) == 2

    def test_highlights_sorted_after_merge(self):
        # Pass highlights out of order; after sort+merge they should come
        # back ordered by timestamp.
        highlights = [
            _h(70.0, dur=1.0),
            _h(50.0, dur=1.0),
            _h(60.0, dur=1.0),
        ]

        result = compute_clip_boundaries(highlights, video_duration=600.0)

        assert result
        for b in result:
            timestamps = [h.timestamp for h in b.highlights]
            assert timestamps == sorted(timestamps)

    def test_distant_highlights_do_not_merge(self):
        # Two highlights far apart — should produce two separate boundaries.
        # CLIP_MERGE_GAP_SECONDS=10, CLIP_CONTEXT_AFTER_SECONDS=20,
        # CLIP_CONTEXT_BEFORE_SECONDS=20 → gap > 50 keeps them separate.
        highlights = [_h(50.0, dur=1.0), _h(200.0, dur=1.0)]

        result = compute_clip_boundaries(highlights, video_duration=600.0)

        assert len(result) == 2
        # Each boundary should only contain its own highlight.
        for b in result:
            assert len(b.highlights) == 1
            assert b.start_time <= b.highlights[0].timestamp <= b.end_time
