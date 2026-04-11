"""JSON metadata persistence for clips."""

import json
from datetime import datetime
from pathlib import Path
from typing import List

from clipcutter.config import DIR_METADATA
from clipcutter.models import ClipMetadata


def save_metadata(clips: List[ClipMetadata], source_video: str,
                  output_dir: Path) -> Path:
    """Save clip metadata to JSON file."""
    meta_dir = output_dir / DIR_METADATA
    meta_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(source_video).stem
    meta_path = meta_dir / f"{stem}_clips.json"

    data = {
        "source_video": source_video,
        "processed_at": datetime.now().isoformat(timespec="seconds"),
        "clip_count": len(clips),
        "clips": [c.to_dict() for c in clips],
    }

    meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return meta_path


def load_metadata(metadata_path: Path) -> List[ClipMetadata]:
    """Load clip metadata from a JSON file."""
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    return [ClipMetadata.from_dict(c) for c in data["clips"]]


def load_metadata_dict(metadata_path: Path) -> dict:
    """Load the full metadata dict from a JSON file."""
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def update_clip_status(metadata_path: Path, filename: str, status: str) -> None:
    """Update a single clip's status in the metadata file."""
    data = json.loads(metadata_path.read_text(encoding="utf-8"))

    for clip in data["clips"]:
        if clip["filename"] == filename:
            clip["status"] = status
            break

    # Atomic write via temp file
    tmp = metadata_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(metadata_path)


def update_clip_custom_name(metadata_path: Path, filename: str,
                            custom_name: str) -> None:
    """Update a single clip's custom name in the metadata file."""
    data = json.loads(metadata_path.read_text(encoding="utf-8"))

    for clip in data["clips"]:
        if clip["filename"] == filename:
            clip["custom_name"] = custom_name
            break

    # Atomic write via temp file
    tmp = metadata_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(metadata_path)


def update_clip_encoding(metadata_path: Path, filename: str,
                         encoded_filename: str, preset: str) -> None:
    """Update a single clip's encoding info in the metadata file."""
    data = json.loads(metadata_path.read_text(encoding="utf-8"))

    for clip in data["clips"]:
        if clip["filename"] == filename:
            clip["encoded_filename"] = encoded_filename
            clip["encoding_preset"] = preset
            break

    # Atomic write via temp file
    tmp = metadata_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(metadata_path)


def clear_clip_encoding(metadata_path: Path, filename: str) -> None:
    """Clear encoding info for a clip in the metadata file."""
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    for clip in data["clips"]:
        if clip["filename"] == filename:
            clip["encoded_filename"] = None
            clip["encoding_preset"] = None
            break
    tmp = metadata_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(metadata_path)


def update_clip_youtube(metadata_path: Path, filename: str,
                        video_id: str, url: str,
                        status: str = "uploaded") -> None:
    """Update a single clip's YouTube upload info in the metadata file."""
    data = json.loads(metadata_path.read_text(encoding="utf-8"))

    for clip in data["clips"]:
        if clip["filename"] == filename:
            clip["youtube_video_id"] = video_id
            clip["youtube_url"] = url
            clip["youtube_upload_status"] = status
            break

    # Atomic write via temp file
    tmp = metadata_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(metadata_path)


def update_clip_duration(metadata_path: Path, filename: str, duration: float) -> None:
    """Update a single clip's duration in the metadata file."""
    data = json.loads(metadata_path.read_text(encoding="utf-8"))

    for clip in data["clips"]:
        if clip["filename"] == filename:
            clip["duration"] = round(duration, 4)
            break

    tmp = metadata_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(metadata_path)
