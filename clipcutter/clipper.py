"""Clip boundary calculation, overlap merging, silence trimming, and extraction."""

from pathlib import Path
from typing import List

import numpy as np

from clipcutter.audio import extract_clip
from clipcutter import config
from clipcutter.features import AudioFeatures, time_to_frames, frames_to_time
from clipcutter.models import ClipBoundary, ClipMetadata, DetectionType, Highlight


def compute_clip_boundaries(highlights: List[Highlight],
                            video_duration: float) -> List[ClipBoundary]:
    """Compute clip boundaries from highlights with context padding and merging."""
    if not highlights:
        return []

    # Sort by timestamp
    highlights = sorted(highlights, key=lambda h: h.timestamp)

    # Create initial boundaries with context
    boundaries = []
    for h in highlights:
        start = max(0, h.timestamp - config.CLIP_CONTEXT_BEFORE_SECONDS)
        end = min(video_duration, h.timestamp + h.duration + config.CLIP_CONTEXT_AFTER_SECONDS)

        # Enforce minimum length
        if end - start < config.CLIP_MIN_LENGTH_SECONDS:
            center = h.timestamp + h.duration / 2
            half = config.CLIP_MIN_LENGTH_SECONDS / 2
            start = max(0, center - half)
            end = min(video_duration, center + half)

        boundaries.append(ClipBoundary(
            start_time=start,
            end_time=end,
            highlights=[h],
        ))

    # Merge overlapping or nearby clips
    merged = [boundaries[0]]
    for b in boundaries[1:]:
        prev = merged[-1]
        if b.start_time <= prev.end_time + config.CLIP_MERGE_GAP_SECONDS:
            prev.end_time = max(prev.end_time, b.end_time)
            prev.highlights.extend(b.highlights)
        else:
            merged.append(b)

    # Split clips that exceed max length
    final = []
    for b in merged:
        if b.duration <= config.CLIP_MAX_LENGTH_SECONDS:
            final.append(b)
        else:
            final.extend(_split_long_clip(b, video_duration))

    return final


def _split_long_clip(clip: ClipBoundary,
                     video_duration: float) -> List[ClipBoundary]:
    """Split a clip that exceeds max length at the largest inter-highlight gap."""
    highlights = sorted(clip.highlights, key=lambda h: h.timestamp)

    if len(highlights) <= 1:
        # Can't split meaningfully, just truncate
        return [ClipBoundary(
            start_time=clip.start_time,
            end_time=min(clip.start_time + config.CLIP_MAX_LENGTH_SECONDS, video_duration),
            highlights=clip.highlights,
        )]

    # Find the largest gap between consecutive highlights
    best_gap = 0
    best_idx = 0
    for i in range(len(highlights) - 1):
        gap = highlights[i + 1].timestamp - (highlights[i].timestamp + highlights[i].duration)
        if gap > best_gap:
            best_gap = gap
            best_idx = i

    # Split at the gap midpoint
    split_point = (
        highlights[best_idx].timestamp + highlights[best_idx].duration +
        highlights[best_idx + 1].timestamp
    ) / 2

    left_highlights = highlights[:best_idx + 1]
    right_highlights = highlights[best_idx + 1:]

    left_end = min(split_point, clip.start_time + config.CLIP_MAX_LENGTH_SECONDS)
    right_start = max(split_point, clip.end_time - config.CLIP_MAX_LENGTH_SECONDS)

    result = []
    left = ClipBoundary(
        start_time=clip.start_time,
        end_time=left_end,
        highlights=left_highlights,
    )
    if left.duration >= config.CLIP_MIN_LENGTH_SECONDS:
        if left.duration > config.CLIP_MAX_LENGTH_SECONDS:
            result.extend(_split_long_clip(left, video_duration))
        else:
            result.append(left)

    right = ClipBoundary(
        start_time=right_start,
        end_time=clip.end_time,
        highlights=right_highlights,
    )
    if right.duration >= config.CLIP_MIN_LENGTH_SECONDS:
        if right.duration > config.CLIP_MAX_LENGTH_SECONDS:
            result.extend(_split_long_clip(right, video_duration))
        else:
            result.append(right)

    return result if result else [ClipBoundary(
        start_time=clip.start_time,
        end_time=min(clip.start_time + config.CLIP_MAX_LENGTH_SECONDS, video_duration),
        highlights=clip.highlights,
    )]


def compute_fallback_clip(video_duration: float) -> List[ClipBoundary]:
    """Extract the last ~5 minutes as a fallback when no highlights are found."""
    fallback_len = min(config.FALLBACK_DURATION_SECONDS, video_duration)
    start = max(0, video_duration - fallback_len)

    return [ClipBoundary(
        start_time=start,
        end_time=video_duration,
        highlights=[Highlight(
            timestamp=start,
            duration=fallback_len,
            detection_type=DetectionType.FALLBACK,
            raw_score=0.0,
            confidence=0.1,
        )],
    )]


def trim_silence(boundary: ClipBoundary, features: AudioFeatures) -> ClipBoundary:
    """Trim leading/trailing silence from a clip boundary."""
    rms = features.rms
    sr = features.sample_rate
    hop = features.hop_length

    # Convert dB threshold to linear
    silence_linear = 10 ** (config.SILENCE_THRESHOLD_DB / 20.0)

    check_frames = time_to_frames(config.SILENCE_CHECK_SECONDS, sr, hop)

    start_frame = time_to_frames(boundary.start_time, sr, hop)
    end_frame = min(time_to_frames(boundary.end_time, sr, hop), len(rms) - 1)

    # Trim leading silence
    new_start_frame = start_frame
    trim_end = min(start_frame + check_frames, end_frame)
    for f in range(start_frame, trim_end):
        if f < len(rms) and rms[f] > silence_linear:
            break
        new_start_frame = f + 1

    # Trim trailing silence
    new_end_frame = end_frame
    trim_start = max(end_frame - check_frames, new_start_frame)
    for f in range(end_frame, trim_start, -1):
        if f < len(rms) and rms[f] > silence_linear:
            break
        new_end_frame = f - 1

    new_start = frames_to_time(new_start_frame, sr, hop)
    new_end = frames_to_time(new_end_frame, sr, hop)

    # Don't trim below minimum length
    if new_end - new_start < config.CLIP_MIN_LENGTH_SECONDS:
        return boundary

    return ClipBoundary(
        start_time=new_start,
        end_time=new_end,
        highlights=boundary.highlights,
    )


def format_timestamp(seconds: float) -> str:
    """Format seconds as XXmYYs (e.g., 02m30s)."""
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m:02d}m{s:02d}s"


def format_duration(seconds: float) -> str:
    """Format seconds as MM:SS (e.g., 02:30)."""
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m:02d}:{s:02d}"


def extract_clips(video_path: Path, boundaries: List[ClipBoundary],
                  output_dir: Path) -> List[ClipMetadata]:
    """Extract all clips from video and return metadata."""
    video_stem = video_path.stem
    clip_dir = output_dir / config.DIR_CLIPS / config.DIR_PENDING / video_stem
    clip_dir.mkdir(parents=True, exist_ok=True)

    metadata_list = []
    for i, boundary in enumerate(boundaries, 1):
        start_fmt = format_timestamp(boundary.start_time)
        end_fmt = format_timestamp(boundary.end_time)
        suffix = video_path.suffix
        filename = f"clip_{i:03d}_{start_fmt}-{end_fmt}{suffix}"
        clip_path = clip_dir / filename

        extract_clip(video_path, boundary.start_time, boundary.end_time, clip_path)

        meta = ClipMetadata(
            filename=filename,
            source_video=video_path.name,
            start_time=boundary.start_time,
            end_time=boundary.end_time,
            duration=boundary.duration,
            detection_reasons=boundary.detection_reasons,
            confidence=boundary.confidence,
        )
        metadata_list.append(meta)

    return metadata_list
