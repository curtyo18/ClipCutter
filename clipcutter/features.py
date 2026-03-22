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
    how unusual a frame is compared to recent history.
    """
    eps = 1e-10
    n = len(signal)
    zscore = np.zeros(n)

    # Use cumulative sums for efficient rolling stats
    cumsum = np.cumsum(signal)
    cumsum2 = np.cumsum(signal ** 2)

    # Compare signal[i] against baseline signal[start:i] (excludes i itself)
    for i in range(1, n):
        start = max(0, i - window_frames)
        count = i - start  # number of elements in signal[start:i]
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


def rolling_window_frames(sr: int = AUDIO_SAMPLE_RATE,
                          hop_length: int = HOP_LENGTH) -> int:
    """Number of frames in the rolling baseline window."""
    return int(ROLLING_WINDOW_SECONDS * sr / hop_length)
