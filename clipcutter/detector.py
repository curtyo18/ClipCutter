"""Highlight detection: four detectors + scoring/ranking system."""

from typing import List

import librosa
import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import find_peaks

from clipcutter.config import (
    COINCIDENCE_BONUS,
    COINCIDENCE_WINDOW_SECONDS,
    LAUGHTER_AUTOCORR_MAX_FREQ,
    LAUGHTER_AUTOCORR_MIN_FREQ,
    LAUGHTER_AUTOCORR_THRESHOLD,
    LAUGHTER_CENTROID_FLOOR_RATIO,
    LAUGHTER_ENERGY_FLOOR_RATIO,
    LAUGHTER_MFCC_VARIANCE_THRESHOLD,
    LAUGHTER_MIN_DURATION_SECONDS,
    MAX_CLIPS_PER_VIDEO,
    MIN_CONFIDENCE_THRESHOLD,
    MULTI_VOICE_BONUS,
    ONSET_STRENGTH_MIN_RATIO,
    ONSET_STRENGTH_ZSCORE,
    SHOUTING_CENTROID_ZSCORE,
    SHOUTING_ENERGY_ZSCORE,
    SHOUTING_MIN_DURATION_SECONDS,
    SUSTAINED_INTENSITY_BONUS,
    SUSTAINED_INTENSITY_SECONDS,
    VOLUME_MIN_DURATION_SECONDS,
    VOLUME_ZSCORE_THRESHOLD,
    WEIGHT_LAUGHTER,
    WEIGHT_SHOUTING,
    WEIGHT_SUDDEN_NOISE,
    WEIGHT_VOLUME,
)
from clipcutter.features import (
    AudioFeatures,
    compute_rolling_zscore,
    frames_to_time,
    rolling_window_frames,
    time_to_frames,
)
from clipcutter.models import DetectionType, Highlight


def detect_highlights(features: AudioFeatures,
                      sensitivity: float = 1.0) -> List[Highlight]:
    """Run all detectors and return scored, filtered highlights."""
    highlights = []
    highlights.extend(_detect_volume_spikes(features, sensitivity))
    highlights.extend(_detect_laughter(features, sensitivity))
    highlights.extend(_detect_shouting(features, sensitivity))
    highlights.extend(_detect_sudden_noises(features, sensitivity))

    if not highlights:
        return []

    highlights = _score_and_filter(highlights, features)
    return highlights


# ---------------------------------------------------------------------------
# Volume Spike Detection
# ---------------------------------------------------------------------------

def _detect_volume_spikes(features: AudioFeatures,
                          sensitivity: float) -> List[Highlight]:
    rms = features.rms
    win = rolling_window_frames(features.sample_rate, features.hop_length)
    zscore = compute_rolling_zscore(rms, win)

    threshold = VOLUME_ZSCORE_THRESHOLD / sensitivity
    min_dist = max(1, int(1.0 * features.sample_rate / features.hop_length))

    peaks, properties = find_peaks(zscore, height=threshold, distance=min_dist)

    highlights = []
    half_thresh = threshold / 2.0

    for peak in peaks:
        peak_z = zscore[peak]

        # Expand outward to find duration
        left = peak
        while left > 0 and zscore[left - 1] > half_thresh:
            left -= 1
        right = peak
        while right < len(zscore) - 1 and zscore[right + 1] > half_thresh:
            right += 1

        t_start = frames_to_time(left, features.sample_rate, features.hop_length)
        t_end = frames_to_time(right, features.sample_rate, features.hop_length)
        duration = t_end - t_start

        if duration < VOLUME_MIN_DURATION_SECONDS:
            continue

        timestamp = frames_to_time(peak, features.sample_rate, features.hop_length)
        highlights.append(Highlight(
            timestamp=timestamp,
            duration=duration,
            detection_type=DetectionType.VOLUME_SPIKE,
            raw_score=float(peak_z),
            details={"zscore": float(peak_z), "duration": duration},
        ))

    return highlights


# ---------------------------------------------------------------------------
# Laughter Detection
# ---------------------------------------------------------------------------

def _detect_laughter(features: AudioFeatures,
                     sensitivity: float) -> List[Highlight]:
    rms = features.rms
    sr = features.sample_rate
    hop = features.hop_length

    # Window and hop in frames
    window_sec = 3.0
    hop_sec = 1.5
    win_frames = max(1, int(window_sec * sr / hop))
    hop_frames = max(1, int(hop_sec * sr / hop))

    median_energy = float(np.median(rms))
    median_centroid = float(np.median(features.spectral_centroid))
    threshold = LAUGHTER_AUTOCORR_THRESHOLD / sensitivity

    # Lag range for 2-8 Hz periodicity
    min_lag = max(1, int(sr / (hop * LAUGHTER_AUTOCORR_MAX_FREQ)))
    max_lag = int(sr / (hop * LAUGHTER_AUTOCORR_MIN_FREQ))

    # Pre-compute per-frame MFCC delta magnitude for spectral variability check
    mfcc_delta = np.diff(features.mfccs, axis=1)
    mfcc_delta_mag = np.sqrt(np.sum(mfcc_delta ** 2, axis=0))
    mfcc_delta_median = float(np.median(mfcc_delta_mag)) + 1e-10

    flagged_windows = []  # (start_frame, end_frame, score)

    for start in range(0, len(rms) - win_frames + 1, hop_frames):
        end = start + win_frames
        window_rms = rms[start:end]

        # Energy floor: laughter is louder than average
        if np.mean(window_rms) < median_energy * LAUGHTER_ENERGY_FLOOR_RATIO:
            continue

        # Autocorrelation of RMS envelope in this window
        autocorr = librosa.autocorrelate(window_rms, max_size=max_lag + 1)
        if autocorr[0] < 1e-10:
            continue
        autocorr = autocorr / autocorr[0]  # Normalize

        # Check for peaks in the laughter frequency range
        if min_lag >= len(autocorr) or max_lag >= len(autocorr):
            continue
        lag_region = autocorr[min_lag:max_lag + 1]
        if len(lag_region) == 0:
            continue

        peak_val = float(np.max(lag_region))
        if peak_val < threshold:
            continue

        # Spectral centroid must be meaningfully elevated (not just above median)
        window_centroid = np.mean(features.spectral_centroid[start:end])
        if window_centroid < median_centroid * LAUGHTER_CENTROID_FLOOR_RATIO:
            continue

        # MFCC variability check: laughter has rapid spectral changes
        # unlike steady-state music or game ambience
        delta_end = min(end, len(mfcc_delta_mag))
        if delta_end > start:
            window_mfcc_var = float(np.mean(mfcc_delta_mag[start:delta_end]))
            if window_mfcc_var < mfcc_delta_median * LAUGHTER_MFCC_VARIANCE_THRESHOLD:
                continue

        energy_ratio = float(np.mean(window_rms) / (median_energy + 1e-10))
        score = peak_val * energy_ratio
        flagged_windows.append((start, end, score))

    # Merge adjacent flagged windows
    highlights = _merge_flagged_regions(
        flagged_windows, features, DetectionType.LAUGHTER,
        LAUGHTER_MIN_DURATION_SECONDS,
    )
    return highlights


def _merge_flagged_regions(flagged: list, features: AudioFeatures,
                           det_type: DetectionType,
                           min_duration: float) -> List[Highlight]:
    """Merge overlapping/adjacent flagged windows into highlight regions."""
    if not flagged:
        return []

    flagged.sort(key=lambda x: x[0])
    merged = [(flagged[0][0], flagged[0][1], flagged[0][2])]

    for start, end, score in flagged[1:]:
        prev_start, prev_end, prev_score = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end), max(prev_score, score))
        else:
            merged.append((start, end, score))

    highlights = []
    for start, end, score in merged:
        t_start = frames_to_time(start, features.sample_rate, features.hop_length)
        t_end = frames_to_time(end, features.sample_rate, features.hop_length)
        duration = t_end - t_start
        if duration < min_duration:
            continue

        mid = (start + end) // 2
        timestamp = frames_to_time(mid, features.sample_rate, features.hop_length)
        highlights.append(Highlight(
            timestamp=timestamp,
            duration=duration,
            detection_type=det_type,
            raw_score=float(score),
            details={"duration": duration, "peak_autocorr": float(score)},
        ))

    return highlights


# ---------------------------------------------------------------------------
# Shouting / Raised Voice Detection
# ---------------------------------------------------------------------------

def _detect_shouting(features: AudioFeatures,
                     sensitivity: float) -> List[Highlight]:
    win = rolling_window_frames(features.sample_rate, features.hop_length)
    rms_z = compute_rolling_zscore(features.rms, win)
    centroid_z = compute_rolling_zscore(features.spectral_centroid, win)

    energy_thresh = SHOUTING_ENERGY_ZSCORE / sensitivity
    centroid_thresh = SHOUTING_CENTROID_ZSCORE / sensitivity

    # Find frames where both conditions hold
    mask = (rms_z > energy_thresh) & (centroid_z > centroid_thresh)

    # Group consecutive True frames into regions
    regions = _contiguous_regions(mask)

    min_frames = time_to_frames(
        SHOUTING_MIN_DURATION_SECONDS,
        features.sample_rate, features.hop_length,
    )

    highlights = []
    for start, end in regions:
        if (end - start) < min_frames:
            continue

        t_start = frames_to_time(start, features.sample_rate, features.hop_length)
        t_end = frames_to_time(end, features.sample_rate, features.hop_length)
        duration = t_end - t_start

        region_score = float(np.mean(rms_z[start:end] * centroid_z[start:end]))
        mid = (start + end) // 2
        timestamp = frames_to_time(mid, features.sample_rate, features.hop_length)

        highlights.append(Highlight(
            timestamp=timestamp,
            duration=duration,
            detection_type=DetectionType.SHOUTING,
            raw_score=region_score,
            details={
                "mean_rms_zscore": float(np.mean(rms_z[start:end])),
                "mean_centroid_zscore": float(np.mean(centroid_z[start:end])),
                "duration": duration,
            },
        ))

    return highlights


def _contiguous_regions(mask: np.ndarray) -> List[tuple]:
    """Find contiguous True regions in a boolean array.

    Returns list of (start, end) frame indices.
    """
    if len(mask) == 0:
        return []

    d = np.diff(mask.astype(int))
    starts = np.where(d == 1)[0] + 1
    ends = np.where(d == -1)[0] + 1

    # Handle edge cases
    if mask[0]:
        starts = np.insert(starts, 0, 0)
    if mask[-1]:
        ends = np.append(ends, len(mask))

    return list(zip(starts, ends))


# ---------------------------------------------------------------------------
# Sudden Noise Detection
# ---------------------------------------------------------------------------

def _local_onset_median(onset: np.ndarray, med_win: int) -> np.ndarray:
    """Per-frame local median of ``onset`` over a window of ``med_win`` frames.

    Equivalent to the previous implementation::

        padded = np.pad(onset, (med_win // 2, med_win // 2), mode='reflect')
        return np.array([np.median(padded[i:i + med_win])
                         for i in range(len(onset))])

    scipy's ``median_filter(..., mode='mirror')`` matches numpy's
    ``np.pad(mode='reflect')`` boundary handling. For odd ``med_win`` the two
    implementations agree exactly (max abs diff = 0); for even ``med_win``
    scipy picks one of the two middle elements while ``np.median`` averages
    them, so we fall back to the Python loop in that less-common case to
    preserve equivalence.
    """
    n = len(onset)
    if n == 0:
        return np.zeros(0)
    if med_win <= 1:
        # 1-sized window: median = the value itself.
        return onset.astype(float, copy=True)
    if med_win % 2 == 1:
        return median_filter(onset, size=med_win, mode="mirror")
    # Even window: preserve exact np.median (mean of two middles) semantics.
    padded = np.pad(onset, (med_win // 2, med_win // 2), mode="reflect")
    return np.array([np.median(padded[i:i + med_win]) for i in range(n)])


def _detect_sudden_noises(features: AudioFeatures,
                          sensitivity: float) -> List[Highlight]:
    onset = features.onset_strength
    win = rolling_window_frames(features.sample_rate, features.hop_length)
    onset_z = compute_rolling_zscore(onset, win)

    # Local median ratio (5-second window)
    med_win = max(1, int(5.0 * features.sample_rate / features.hop_length))
    local_median = _local_onset_median(onset, med_win)
    local_median = np.maximum(local_median, 1e-10)
    onset_ratio = onset / local_median

    threshold = ONSET_STRENGTH_ZSCORE / sensitivity
    ratio_thresh = ONSET_STRENGTH_MIN_RATIO / sensitivity
    min_dist = max(1, int(0.5 * features.sample_rate / features.hop_length))

    # Find peaks in z-score, then verify ratio threshold in the loop below
    peaks, _ = find_peaks(
        onset_z,
        height=threshold,
        prominence=2.0,
        distance=min_dist,
    )

    highlights = []
    for peak in peaks:
        if onset_ratio[peak] < ratio_thresh:
            continue

        timestamp = frames_to_time(peak, features.sample_rate, features.hop_length)
        score = float(onset_z[peak] * onset_ratio[peak])

        highlights.append(Highlight(
            timestamp=timestamp,
            duration=0.5,  # Sudden noises are brief
            detection_type=DetectionType.SUDDEN_NOISE,
            raw_score=score,
            details={
                "onset_zscore": float(onset_z[peak]),
                "onset_ratio": float(onset_ratio[peak]),
            },
        ))

    return highlights


# ---------------------------------------------------------------------------
# Scoring and Filtering
# ---------------------------------------------------------------------------

def _score_and_filter(highlights: List[Highlight],
                      features: AudioFeatures) -> List[Highlight]:
    """Normalize, weight, apply bonuses, filter, and rank highlights."""
    if not highlights:
        return []

    # Group by detection type for normalization
    type_groups: dict = {}
    for h in highlights:
        type_groups.setdefault(h.detection_type, []).append(h)

    # Normalize raw scores within each type to [0, 1]
    for det_type, group in type_groups.items():
        scores = [h.raw_score for h in group]
        min_s, max_s = min(scores), max(scores)
        range_s = max_s - min_s
        if range_s < 1e-10:
            # Single highlight or all same score — assign high confidence
            for h in group:
                h.confidence = 0.8
        else:
            for h in group:
                h.confidence = (h.raw_score - min_s) / range_s

    # Apply type-specific weights as multipliers
    # Weights boost stronger signal types without creating an artificial floor
    weight_map = {
        DetectionType.VOLUME_SPIKE: WEIGHT_VOLUME,
        DetectionType.LAUGHTER: WEIGHT_LAUGHTER,
        DetectionType.SHOUTING: WEIGHT_SHOUTING,
        DetectionType.SUDDEN_NOISE: WEIGHT_SUDDEN_NOISE,
    }
    for h in highlights:
        weight = weight_map.get(h.detection_type, 0.2)
        h.confidence *= (1.0 + weight)

    # Sustained intensity bonus
    for h in highlights:
        if h.duration >= SUSTAINED_INTENSITY_SECONDS:
            h.confidence += SUSTAINED_INTENSITY_BONUS

    # Multi-voice overlap bonus (elevated spectral bandwidth)
    bw = features.spectral_bandwidth
    bw_mean = np.mean(bw)
    bw_std = np.std(bw) + 1e-10
    for h in highlights:
        frame = time_to_frames(h.timestamp, features.sample_rate, features.hop_length)
        frame = min(frame, len(bw) - 1)
        bw_z = (bw[frame] - bw_mean) / bw_std
        if bw_z > 1.5:
            h.confidence += MULTI_VOICE_BONUS

    # Coincidence bonus: multiple detection types near same timestamp
    highlights.sort(key=lambda h: h.timestamp)
    for i, h in enumerate(highlights):
        for j in range(i + 1, len(highlights)):
            other = highlights[j]
            if other.timestamp - h.timestamp > COINCIDENCE_WINDOW_SECONDS:
                break
            if other.detection_type != h.detection_type:
                h.confidence += COINCIDENCE_BONUS
                other.confidence += COINCIDENCE_BONUS
                break  # Only one bonus per pair

    # Clamp and filter
    for h in highlights:
        h.confidence = min(1.0, max(0.0, h.confidence))

    highlights = [h for h in highlights if h.confidence >= MIN_CONFIDENCE_THRESHOLD]
    highlights.sort(key=lambda h: -h.confidence)

    # Cap to max clips
    highlights = highlights[:MAX_CLIPS_PER_VIDEO]
    return highlights
