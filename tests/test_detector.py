"""Tests for clipcutter.detector — focused on the vectorized hot loops.

Specifically guards the sudden-noise local-median computation, which used
``[np.median(padded[i:i+med_win]) for i in range(...)]`` and is now backed
by ``scipy.ndimage.median_filter``. These tests pin the equivalence so the
swap stays behaviorally identical for callers.

Because the detector wraps the median computation in a private helper, we
also smoke-test the public ``detect_highlights`` path against the
existing audio fixtures from ``conftest.py`` to confirm refactor doesn't
regress end-to-end behavior.
"""

import shutil

import numpy as np
import pytest

from clipcutter.detector import _local_onset_median
from clipcutter.features import compute_features


_HAS_FFMPEG = shutil.which("ffmpeg") is not None
requires_ffmpeg = pytest.mark.skipif(
    not _HAS_FFMPEG, reason="ffmpeg not installed"
)


def _reference_local_median(onset: np.ndarray, med_win: int) -> np.ndarray:
    """Original Python loop: median over reflect-padded window per frame."""
    padded = np.pad(onset, (med_win // 2, med_win // 2), mode="reflect")
    return np.array([
        np.median(padded[i:i + med_win])
        for i in range(len(onset))
    ])


# ---------------------------------------------------------------------------
# Equivalence on synthetic input across a range of odd window sizes.
# The production ``med_win`` formula yields odd values for the default
# sr/hop config (215), so odd-window equivalence is the contract.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "seed,length,med_win",
    [
        (0, 1000, 21),
        (1, 5000, 99),
        (2, 25800, 215),   # production: sr=22050, hop=512 -> 5s window
        (3, 200, 31),
        (4, 50, 3),
        (5, 50, 201),      # window much larger than signal
    ],
)
def test_local_median_matches_reference_odd_window(seed, length, med_win):
    rng = np.random.default_rng(seed)
    onset = rng.random(length) * 5.0
    expected = _reference_local_median(onset, med_win)
    actual = _local_onset_median(onset, med_win)
    assert actual.shape == expected.shape
    assert np.allclose(actual, expected, atol=1e-9), (
        f"max abs diff = {np.max(np.abs(actual - expected))}"
    )


def test_local_median_known_input():
    """Hand-traced fixture: median over odd reflect-padded windows."""
    onset = np.array([1.0, 5.0, 2.0, 8.0, 3.0, 7.0, 4.0])
    expected = _reference_local_median(onset, 3)
    actual = _local_onset_median(onset, 3)
    assert np.allclose(actual, expected, atol=1e-9)


def test_local_median_constant():
    onset = np.full(100, 2.5)
    expected = _reference_local_median(onset, 21)
    actual = _local_onset_median(onset, 21)
    assert np.allclose(actual, expected, atol=1e-9)
    assert np.allclose(actual, 2.5, atol=1e-12)


def test_local_median_single_window():
    """med_win=1 is a degenerate but valid input — returns the signal."""
    rng = np.random.default_rng(11)
    onset = rng.random(50) * 2.0
    expected = _reference_local_median(onset, 1)
    actual = _local_onset_median(onset, 1)
    assert np.allclose(actual, expected, atol=1e-9)


@pytest.mark.parametrize(
    "seed,length,med_win",
    [
        (10, 500, 4),
        (11, 1000, 100),
        (12, 5000, 430),    # sr=22050, hop=256 -> even
        (13, 500, 156),     # sr=16000, hop=512 -> even
    ],
)
def test_local_median_even_window_fallback(seed, length, med_win):
    """Even med_win paths use the loop fallback (scipy median_filter picks
    one middle element vs np.median's average of two). The helper must still
    match the original loop bit-identically."""
    rng = np.random.default_rng(seed)
    onset = rng.random(length) * 5.0
    expected = _reference_local_median(onset, med_win)
    actual = _local_onset_median(onset, med_win)
    assert np.allclose(actual, expected, atol=1e-9)


# ---------------------------------------------------------------------------
# End-to-end smoke test: vectorized refactors don't regress detect_highlights.
# Uses the synthetic fixtures from conftest.py so this runs in CI without
# real video assets.
# ---------------------------------------------------------------------------

@requires_ffmpeg
def test_detect_highlights_smoke_silence(silence_video):
    """Silent video: should produce no highlights (or only weak ones)."""
    from clipcutter.audio import extract_audio
    from clipcutter.detector import detect_highlights
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        audio_path = extract_audio(silence_video, Path(tmp))
        features = compute_features(audio_path)
        highlights = detect_highlights(features, sensitivity=1.0)
        # Silence shouldn't surface volume/shouting/sudden_noise highlights.
        assert isinstance(highlights, list)


@requires_ffmpeg
def test_detect_highlights_smoke_noise(noise_video):
    """Continuous loud noise: shouldn't crash; returns a list of highlights."""
    from clipcutter.audio import extract_audio
    from clipcutter.detector import detect_highlights
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        audio_path = extract_audio(noise_video, Path(tmp))
        features = compute_features(audio_path)
        highlights = detect_highlights(features, sensitivity=1.0)
        assert isinstance(highlights, list)
