"""Audio feature extraction pipeline."""

from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

from clipcutter.config import (
    AUDIO_SAMPLE_RATE,
    FRAME_LENGTH,
    HOP_LENGTH,
    ROLLING_WINDOW_SECONDS,
)


@dataclass
class AudioFeatures:
    """All computed features for an audio file."""
    sample_rate: int
    hop_length: int
    duration: float
    rms: np.ndarray                # (n_frames,)
    spectral_centroid: np.ndarray  # (n_frames,)
    spectral_bandwidth: np.ndarray # (n_frames,)
    onset_strength: np.ndarray     # (n_frames,)
    mfccs: np.ndarray              # (n_mfcc, n_frames)


def compute_features(audio_path: Path) -> AudioFeatures:
    """Compute all audio features in a single pass."""
    y, sr = librosa.load(str(audio_path), sr=AUDIO_SAMPLE_RATE, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)

    rms = librosa.feature.rms(
        y=y, frame_length=FRAME_LENGTH, hop_length=HOP_LENGTH
    )[0]

    spectral_centroid = librosa.feature.spectral_centroid(
        y=y, sr=sr, hop_length=HOP_LENGTH
    )[0]

    spectral_bandwidth = librosa.feature.spectral_bandwidth(
        y=y, sr=sr, hop_length=HOP_LENGTH
    )[0]

    onset_strength = librosa.onset.onset_strength(
        y=y, sr=sr, hop_length=HOP_LENGTH
    )

    mfccs = librosa.feature.mfcc(
        y=y, sr=sr, n_mfcc=13, hop_length=HOP_LENGTH
    )

    return AudioFeatures(
        sample_rate=sr,
        hop_length=HOP_LENGTH,
        duration=duration,
        rms=rms,
        spectral_centroid=spectral_centroid,
        spectral_bandwidth=spectral_bandwidth,
        onset_strength=onset_strength,
        mfccs=mfccs,
    )


def frames_to_time(frame_index: int, sr: int = AUDIO_SAMPLE_RATE,
                    hop_length: int = HOP_LENGTH) -> float:
    """Convert feature frame index to time in seconds."""
    return librosa.frames_to_time(frame_index, sr=sr, hop_length=hop_length)


def time_to_frames(time_sec: float, sr: int = AUDIO_SAMPLE_RATE,
                    hop_length: int = HOP_LENGTH) -> int:
    """Convert time in seconds to nearest frame index."""
    return int(librosa.time_to_frames(time_sec, sr=sr, hop_length=hop_length))


def compute_rolling_zscore(signal: np.ndarray, window_frames: int) -> np.ndarray:
    """Compute z-score of each frame relative to a rolling window baseline.

    Uses a causal (backward-looking) window so z-scores reflect
    how unusual a frame is compared to recent history. Frames with fewer
    than 2 prior samples in the window stay at 0.

    Vectorized via shifted cumulative sums — bit-identical to the prior
    Python frame-loop implementation within float tolerance.
    """
    eps = 1e-10
    n = len(signal)
    if n == 0:
        return np.zeros(0)

    # For each frame i, compare signal[i] against the baseline signal[start:i]
    # where start = max(0, i - window_frames). Using a prepended-0 cumsum lets
    # us compute sum(signal[start:i]) as ext_cumsum[i] - ext_cumsum[start].
    cumsum = np.cumsum(signal)
    cumsum2 = np.cumsum(signal ** 2)
    ext_cumsum = np.concatenate(([0.0], cumsum))     # length n+1
    ext_cumsum2 = np.concatenate(([0.0], cumsum2))   # length n+1

    idx = np.arange(n)
    start = np.maximum(0, idx - window_frames)
    count = idx - start

    s = ext_cumsum[idx] - ext_cumsum[start]
    s2 = ext_cumsum2[idx] - ext_cumsum2[start]

    # count < 2 frames keep zscore = 0; mask them out and use a safe divisor.
    safe = count >= 2
    safe_count = np.where(safe, count, 1)
    mean = s / safe_count
    var = np.maximum(s2 / safe_count - mean ** 2, 0.0)
    std = np.sqrt(var) + eps

    zscore = np.where(safe, (signal - mean) / std, 0.0)
    return zscore


def rolling_window_frames(sr: int = AUDIO_SAMPLE_RATE,
                          hop_length: int = HOP_LENGTH) -> int:
    """Number of frames in the rolling baseline window."""
    return int(ROLLING_WINDOW_SECONDS * sr / hop_length)
