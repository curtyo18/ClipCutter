"""Tests for clipcutter.features — focused on the vectorized hot loops.

These tests compare the current ``compute_rolling_zscore`` implementation
against an inline reference port of the original Python frame-loop version.
They exist to guard the refactor: as long as both match within float
tolerance on a battery of synthetic inputs, the vectorized form is
behaviorally equivalent to callers.
"""

import numpy as np
import pytest

from clipcutter.features import compute_rolling_zscore


def _reference_compute_rolling_zscore(signal: np.ndarray,
                                      window_frames: int) -> np.ndarray:
    """Reference implementation: the original Python frame loop.

    Kept here verbatim so the test compares the production implementation
    against a known-good baseline that captures the pre-refactor behavior.
    """
    eps = 1e-10
    n = len(signal)
    zscore = np.zeros(n)

    cumsum = np.cumsum(signal)
    cumsum2 = np.cumsum(signal ** 2)

    for i in range(1, n):
        start = max(0, i - window_frames)
        count = i - start
        if count < 2:
            continue
        if start == 0:
            s = cumsum[i - 1]
            s2 = cumsum2[i - 1]
        else:
            s = cumsum[i - 1] - cumsum[start - 1]
            s2 = cumsum2[i - 1] - cumsum2[start - 1]
        mean = s / count
        var = s2 / count - mean ** 2
        std = np.sqrt(max(var, 0)) + eps
        zscore[i] = (signal[i] - mean) / std

    return zscore


# ---------------------------------------------------------------------------
# Equivalence: production impl matches the reference frame-loop on a battery
# of synthetic inputs (random, edge sizes, degenerate inputs).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "seed,length,window",
    [
        (0, 1000, 50),
        (1, 5000, 200),
        (2, 25800, 1291),      # 10-min @ 22050/512, ROLLING_WINDOW_SECONDS=30
        (3, 100, 10),
        (4, 50, 1000),         # window much larger than signal
        (5, 3, 100),           # signal shorter than window
    ],
)
def test_rolling_zscore_matches_reference(seed, length, window):
    rng = np.random.default_rng(seed)
    signal = rng.normal(0.0, 1.0, length).astype(np.float64)
    expected = _reference_compute_rolling_zscore(signal, window)
    actual = compute_rolling_zscore(signal, window)
    assert actual.shape == expected.shape
    assert np.allclose(actual, expected, atol=1e-9), (
        f"max abs diff = {np.max(np.abs(actual - expected))}"
    )


def test_rolling_zscore_constant_signal():
    """Constant input: std is 0, eps prevents div by 0, all zscores zero."""
    signal = np.full(200, 7.5)
    expected = _reference_compute_rolling_zscore(signal, 50)
    actual = compute_rolling_zscore(signal, 50)
    assert np.allclose(actual, expected, atol=1e-9)
    # And actually zero (signal == mean everywhere meaningful)
    assert np.allclose(actual, 0.0, atol=1e-9)


def test_rolling_zscore_all_zeros():
    signal = np.zeros(500)
    expected = _reference_compute_rolling_zscore(signal, 50)
    actual = compute_rolling_zscore(signal, 50)
    assert np.allclose(actual, expected, atol=1e-9)


def test_rolling_zscore_empty():
    signal = np.array([], dtype=np.float64)
    actual = compute_rolling_zscore(signal, 50)
    assert actual.shape == (0,)


def test_rolling_zscore_single_element():
    """count < 2 path: zscore[0] stays 0 by construction."""
    signal = np.array([3.14])
    expected = _reference_compute_rolling_zscore(signal, 50)
    actual = compute_rolling_zscore(signal, 50)
    assert np.allclose(actual, expected, atol=1e-9)


def test_rolling_zscore_known_spike():
    """A large spike at frame 100 should produce a strongly positive zscore."""
    rng = np.random.default_rng(99)
    signal = rng.normal(0.0, 0.1, 500)
    signal[100] = 10.0
    expected = _reference_compute_rolling_zscore(signal, 50)
    actual = compute_rolling_zscore(signal, 50)
    assert np.allclose(actual, expected, atol=1e-9)
    assert actual[100] > 5.0  # sanity: spike registers as outlier


def test_rolling_zscore_window_exactly_two():
    """Boundary: count >= 2 kicks in starting at frame 2 with window=2."""
    signal = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    expected = _reference_compute_rolling_zscore(signal, 2)
    actual = compute_rolling_zscore(signal, 2)
    assert np.allclose(actual, expected, atol=1e-9)


def test_rolling_zscore_dtype_preserved_or_float():
    """Float32 input: result should still produce comparable numerics."""
    rng = np.random.default_rng(7)
    signal = rng.normal(0.0, 1.0, 500).astype(np.float32)
    expected = _reference_compute_rolling_zscore(signal, 100)
    actual = compute_rolling_zscore(signal, 100)
    # float32 inputs accumulate more error; relax tolerance slightly.
    assert np.allclose(actual, expected, atol=1e-5)
