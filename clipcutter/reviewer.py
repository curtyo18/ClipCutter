"""Interactive clip review workflow."""

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import click

from clipcutter.clipper import format_duration
from clipcutter.config import DIR_CLIPS, DIR_KEPT, DIR_METADATA, DIR_PENDING
from clipcutter.metadata import load_metadata, load_metadata_dict, update_clip_status
from clipcutter.models import ClipMetadata


def review_clips(output_dir: Path, player: str = "auto",
                 source_filter: Optional[str] = None) -> None:
    """Interactive review: watch clips, keep or discard."""
    output_dir = Path(output_dir).resolve()
    pending_dir = output_dir / DIR_CLIPS / DIR_PENDING
    meta_dir = output_dir / DIR_METADATA

    if not pending_dir.exists():
        click.echo("No pending clips found.")
        return

    # Collect all pending clips across source videos
    review_items = []

    for video_dir in sorted(pending_dir.iterdir()):
        if not video_dir.is_dir():
            continue

        video_stem = video_dir.name

        if source_filter and source_filter not in video_stem:
            continue

        # Find metadata file
        meta_path = meta_dir / f"{video_stem}_clips.json"
        if not meta_path.exists():
            click.echo(f"Warning: No metadata for {video_stem}, skipping.")
            continue

        clips = load_metadata(meta_path)
        meta_data = load_metadata_dict(meta_path)
        source_video = meta_data.get("source_video", video_stem)

        # Only include pending clips that still exist on disk
        for clip in clips:
            if clip.status != "pending":
                continue
            clip_path = video_dir / clip.filename
            if clip_path.exists():
                review_items.append((clip, clip_path, meta_path, source_video))

    if not review_items:
        click.echo("No pending clips to review.")
        return

    # Sort by confidence (highest first)
    review_items.sort(key=lambda x: -x[0].confidence)

    sources = set(item[3] for item in review_items)
    click.echo(f"{len(review_items)} pending clip(s) from {len(sources)} video(s).")

    kept = 0
    discarded = 0
    skipped = 0

    for i, (clip, clip_path, meta_path, source_video) in enumerate(review_items, 1):
        click.echo()
        click.echo("━" * 45)
        click.echo(f"  Clip {i}/{len(review_items)} from {source_video}")
        click.echo(f"  File: {clip.filename}")
        click.echo(f"  Time: {format_duration(clip.start_time)} - {format_duration(clip.end_time)} ({clip.duration:.0f}s)")
        click.echo(f"  Detected: {', '.join(clip.detection_reasons)}")
        click.echo(f"  Confidence: {clip.confidence:.2f}")
        click.echo("━" * 45)

        # Open clip in video player
        _play_clip(clip_path, player)

        # Prompt for action
        while True:
            action = click.prompt(
                "  [k]eep / [d]iscard / [s]kip / [r]eplay / [q]uit",
                type=str, default="s",
            ).strip().lower()

            if action in ("k", "keep"):
                _keep_clip(clip, clip_path, meta_path, output_dir)
                kept += 1
                click.echo("  -> Kept.")
                break
            elif action in ("d", "discard"):
                _discard_clip(clip, clip_path, meta_path)
                discarded += 1
                click.echo("  -> Discarded.")
                break
            elif action in ("s", "skip"):
                skipped += 1
                click.echo("  -> Skipped.")
                break
            elif action in ("r", "replay"):
                _play_clip(clip_path, player)
            elif action in ("q", "quit"):
                click.echo(f"\nReview stopped. {kept} kept, {discarded} discarded, "
                          f"{skipped} skipped, "
                          f"{len(review_items) - i} remaining.")
                return
            else:
                click.echo("  Invalid choice. Use k/d/s/r/q.")

    click.echo(f"\nReview complete: {kept} kept, {discarded} discarded, {skipped} skipped.")
    if kept > 0:
        click.echo(f"Kept clips saved to: {output_dir / DIR_CLIPS / DIR_KEPT}")

    # Clean up empty pending subdirectories
    _cleanup_empty_dirs(pending_dir)


def _keep_clip(clip: ClipMetadata, clip_path: Path, meta_path: Path,
               output_dir: Path) -> None:
    """Move clip to kept directory and update metadata."""
    video_stem = clip_path.parent.name
    kept_dir = output_dir / DIR_CLIPS / DIR_KEPT / video_stem
    kept_dir.mkdir(parents=True, exist_ok=True)

    dest = kept_dir / clip.filename
    shutil.move(str(clip_path), str(dest))
    update_clip_status(meta_path, clip.filename, "kept")


def _discard_clip(clip: ClipMetadata, clip_path: Path, meta_path: Path) -> None:
    """Delete clip file and update metadata."""
    clip_path.unlink(missing_ok=True)
    update_clip_status(meta_path, clip.filename, "discarded")


def _play_clip(clip_path: Path, player: str) -> None:
    """Open clip in video player."""
    try:
        if player != "auto":
            subprocess.Popen([player, str(clip_path)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif platform.system() == "Windows":
            os.startfile(str(clip_path))
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(clip_path)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(clip_path)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        click.echo(f"  Could not open player: {e}")
        click.echo(f"  Open manually: {clip_path}")


def _cleanup_empty_dirs(pending_dir: Path) -> None:
    """Remove empty subdirectories from pending."""
    if not pending_dir.exists():
        return
    for d in list(pending_dir.iterdir()):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()


