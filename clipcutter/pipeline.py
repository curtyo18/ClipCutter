"""Orchestrates the full analysis pipeline."""

import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import click

if TYPE_CHECKING:
    from clipcutter.state import ProcessingState

from clipcutter.audio import check_ffmpeg, extract_audio, get_video_duration, NoAudioStreamError
from clipcutter.clipper import (
    compute_clip_boundaries,
    compute_fallback_clip,
    ensure_end_clip,
    extract_clips,
    format_duration,
    trim_silence,
)
from clipcutter.config import AUDIO_SAMPLE_RATE, DIR_CLIPS, DIR_METADATA, DIR_PENDING, VIDEO_EXTENSIONS
from clipcutter.detector import detect_highlights
from clipcutter.features import compute_features
from clipcutter.metadata import save_metadata
from clipcutter.models import ClipMetadata


def process_video(video_path: Path, output_dir: Path,
                  sensitivity: float = 1.0,
                  dry_run: bool = False,
                  overwrite: bool = False) -> List[ClipMetadata]:
    """Process a single video: analyze audio, detect highlights, extract clips."""
    check_ffmpeg()

    video_path = Path(video_path).resolve()
    output_dir = Path(output_dir).resolve()

    if video_path.suffix.lower() not in VIDEO_EXTENSIONS:
        click.echo(f"  Skipping unsupported format: {video_path.name}")
        return []

    click.echo(f"  Analyzing: {video_path.name}")

    # Check for existing clips
    existing_clip_dir = output_dir / DIR_CLIPS / DIR_PENDING / video_path.stem
    existing_meta = output_dir / DIR_METADATA / f"{video_path.stem}_clips.json"
    if existing_clip_dir.exists() and any(existing_clip_dir.iterdir()):
        if overwrite:
            pass  # proceed to cleanup below
        elif not click.confirm(
            f"  Clips already exist for {video_path.name}. Overwrite?",
            default=False,
        ):
            click.echo("  Skipped.")
            return []
        # Clean up old clips
        shutil.rmtree(existing_clip_dir)
        if existing_meta.exists():
            existing_meta.unlink()

    # Get video duration
    video_duration = get_video_duration(video_path)
    click.echo(f"  Duration: {format_duration(video_duration)}")

    # Extract audio to temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="clipcutter_"))
    try:
        click.echo("  Extracting audio...")
        try:
            wav_path = extract_audio(video_path, temp_dir, AUDIO_SAMPLE_RATE)
        except NoAudioStreamError:
            click.echo("  No audio stream found, skipping.")
            return []

        # Compute features
        click.echo("  Computing audio features...", nl=False)
        t0 = time.monotonic()
        features = compute_features(wav_path)
        click.echo(f" ({time.monotonic() - t0:.1f}s)")

        # Detect highlights
        click.echo("  Detecting highlights...", nl=False)
        t0 = time.monotonic()
        highlights = detect_highlights(features, sensitivity)
        click.echo(f" ({time.monotonic() - t0:.1f}s)")

        if highlights:
            click.echo(f"  Found {len(highlights)} highlight(s):")
            for h in highlights:
                click.echo(
                    f"    {h.detection_type.value} at "
                    f"{format_duration(h.timestamp)} "
                    f"(confidence: {h.confidence:.2f})"
                )
        else:
            click.echo("  No highlights detected, using fallback.")

        # Compute clip boundaries
        if highlights:
            boundaries = compute_clip_boundaries(highlights, video_duration)
        else:
            boundaries = compute_fallback_clip(video_duration)

        # Always include the last minute unless already covered
        boundaries = ensure_end_clip(boundaries, video_duration)

        # Trim silence
        boundaries = [trim_silence(b, features) for b in boundaries]

        click.echo(f"  {len(boundaries)} clip(s) to extract:")
        for i, b in enumerate(boundaries, 1):
            click.echo(
                f"    Clip {i}: {format_duration(b.start_time)} - "
                f"{format_duration(b.end_time)} "
                f"({b.duration:.0f}s) [{', '.join(b.detection_reasons)}]"
            )

        if dry_run:
            click.echo("  Dry run — skipping extraction.")
            return []

        # Extract clips
        click.echo("  Extracting clips...")
        clip_metas = extract_clips(video_path, boundaries, output_dir)

        # Save metadata
        meta_path = save_metadata(clip_metas, str(video_path), output_dir)
        click.echo(f"  Metadata saved: {meta_path}")

        return clip_metas

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def process_directory(input_dir: Path, output_dir: Path,
                      sensitivity: float = 1.0,
                      recursive: bool = False,
                      dry_run: bool = False,
                      overwrite: bool = False,
                      progress: Optional["ProcessingState"] = None) -> None:
    """Process all video files in a directory.

    If `progress` is supplied, per-video counters are updated so the FE can
    surface real progress on /api/process/status. The plumbing is an optional
    parameter (rather than a callback) because the only consumer is the web
    route, and threading the state object keeps the call site one line.
    """
    input_dir = Path(input_dir).resolve()

    if recursive:
        video_files = [
            f for f in input_dir.rglob("*")
            if f.suffix.lower() in VIDEO_EXTENSIONS
        ]
    else:
        video_files = [
            f for f in input_dir.iterdir()
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
        ]

    video_files.sort()

    if not video_files:
        click.echo(f"No video files found in {input_dir}")
        if progress is not None:
            progress.set_total(0)
        return

    if progress is not None:
        progress.set_total(len(video_files))

    click.echo(f"Found {len(video_files)} video(s) to process.\n")

    total_clips = 0
    for i, video_path in enumerate(video_files, 1):
        click.echo(f"[{i}/{len(video_files)}] {video_path.name}")
        if progress is not None:
            progress.start_video(video_path.name)
        try:
            clips = process_video(video_path, output_dir, sensitivity, dry_run, overwrite)
            total_clips += len(clips)
        finally:
            if progress is not None:
                progress.finish_video()
        click.echo()

    click.echo(f"Done. {total_clips} clip(s) extracted from {len(video_files)} video(s).")
    if not dry_run and total_clips > 0:
        click.echo(f"Clips saved to: {output_dir / DIR_CLIPS / DIR_PENDING}")
        click.echo(f"Run 'clipcutter review -o {output_dir}' to review.")
