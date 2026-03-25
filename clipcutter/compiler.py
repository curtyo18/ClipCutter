"""Compilation builder: concatenate multiple clips into a single video."""

import subprocess
import tempfile
from pathlib import Path
from typing import List

from clipcutter.audio import get_video_duration


def build_compilation(
    clip_paths: List[Path],
    output_path: Path,
    transition: str = "cut",
    crossfade_duration: float = 0.5,
) -> Path:
    """Build a compilation video from multiple clips.

    Args:
        clip_paths: Ordered list of clip file paths.
        output_path: Output file path.
        transition: "cut" for hard cuts, "crossfade" for fade transitions.
        crossfade_duration: Duration of crossfade in seconds (only for crossfade).

    Returns:
        Path to the output file.

    Raises:
        RuntimeError: If FFmpeg fails.
        ValueError: If fewer than 2 clips provided.
    """
    if len(clip_paths) < 2:
        raise ValueError("Need at least 2 clips for a compilation")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if transition == "crossfade":
        return _build_crossfade(clip_paths, output_path, crossfade_duration)
    else:
        return _build_concat(clip_paths, output_path)


def _build_concat(
    clip_paths: List[Path],
    output_path: Path,
) -> Path:
    """Hard-cut compilation using FFmpeg concat demuxer."""
    # Write concat list to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        for p in clip_paths:
            # FFmpeg concat requires forward slashes and escaped single quotes
            safe = str(p).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe}'\n")
        concat_path = f.name

    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_path,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg concat failed: {result.stderr[-500:]}"
            )
    finally:
        try:
            Path(concat_path).unlink()
        except OSError:
            pass

    return output_path


def _build_crossfade(
    clip_paths: List[Path],
    output_path: Path,
    xfade_dur: float = 0.5,
) -> Path:
    """Crossfade compilation using FFmpeg xfade + acrossfade filters."""
    # Get durations for all clips
    durations = [get_video_duration(p) for p in clip_paths]
    n = len(clip_paths)

    # Build input args
    cmd = ["ffmpeg", "-y"]
    for p in clip_paths:
        cmd.extend(["-i", str(p)])

    # Build filter_complex for chained xfade/acrossfade
    vfilters = []
    afilters = []
    cumulative = durations[0]

    for i in range(1, n):
        offset = max(0, cumulative - xfade_dur)

        # Video chain
        vin = f"[v{i-1}]" if i > 1 else "[0:v]"
        vout = "[vout]" if i == n - 1 else f"[v{i}]"
        vfilters.append(
            f"{vin}[{i}:v]xfade=transition=fade:duration={xfade_dur:.3f}"
            f":offset={offset:.3f}{vout}"
        )

        # Audio chain
        ain = f"[a{i-1}]" if i > 1 else "[0:a]"
        aout = "[aout]" if i == n - 1 else f"[a{i}]"
        afilters.append(
            f"{ain}[{i}:a]acrossfade=d={xfade_dur:.3f}:c1=tri:c2=tri{aout}"
        )

        cumulative = offset + durations[i]

    filter_complex = ";".join(vfilters + afilters)

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        str(output_path),
    ])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg crossfade failed: {result.stderr[-500:]}"
        )

    return output_path


def get_compilation_duration(
    clip_durations: List[float],
    transition: str = "cut",
    crossfade_duration: float = 0.5,
) -> float:
    """Calculate total compilation duration."""
    total = sum(clip_durations)
    if transition == "crossfade" and len(clip_durations) > 1:
        total -= (len(clip_durations) - 1) * crossfade_duration
    return max(0, total)
