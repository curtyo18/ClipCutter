"""JSON metadata persistence for clips."""

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, List

from clipcutter.config import DIR_METADATA
from clipcutter.models import ClipMetadata


# Per-metadata-file locks so concurrent update_clip_* calls don't lose writes
# via interleaved read-mutate-write. The dict itself is guarded by _LOCKS_GUARD.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def mutate_metadata(metadata_path: Path, mutator: Callable[[dict], bool]) -> bool:
    """Read JSON, call mutator(data), atomic temp+rename write.

    mutator returns True if it modified the data (write happens); False to skip
    the write. Returns the mutator's return value (typically: did it find what
    it was looking for).
    """
    lock = _lock_for(metadata_path)
    with lock:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        modified = mutator(data)
        if modified:
            tmp = metadata_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(metadata_path)
        return modified


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


def update_clip_status(metadata_path: Path, filename: str, status: str) -> bool:
    """Update a single clip's status. Returns True if the filename was found."""
    def mutator(data: dict) -> bool:
        for clip in data["clips"]:
            if clip["filename"] == filename:
                clip["status"] = status
                return True
        return False
    return mutate_metadata(metadata_path, mutator)


def update_clip_custom_name(metadata_path: Path, filename: str,
                            custom_name: str) -> bool:
    """Update a single clip's custom name. Returns True if the filename was found."""
    def mutator(data: dict) -> bool:
        for clip in data["clips"]:
            if clip["filename"] == filename:
                clip["custom_name"] = custom_name
                return True
        return False
    return mutate_metadata(metadata_path, mutator)


def update_clip_encoding(metadata_path: Path, filename: str,
                         encoded_filename: str, preset: str) -> bool:
    """Update a single clip's encoding info. Returns True if the filename was found."""
    def mutator(data: dict) -> bool:
        for clip in data["clips"]:
            if clip["filename"] == filename:
                clip["encoded_filename"] = encoded_filename
                clip["encoding_preset"] = preset
                return True
        return False
    return mutate_metadata(metadata_path, mutator)


def clear_clip_encoding(metadata_path: Path, filename: str) -> bool:
    """Clear encoding info for a clip. Returns True if the filename was found."""
    def mutator(data: dict) -> bool:
        for clip in data["clips"]:
            if clip["filename"] == filename:
                clip["encoded_filename"] = None
                clip["encoding_preset"] = None
                return True
        return False
    return mutate_metadata(metadata_path, mutator)


def update_clip_youtube(metadata_path: Path, filename: str,
                        video_id: str, url: str,
                        status: str = "uploaded") -> bool:
    """Update a single clip's YouTube upload info. Returns True if the filename was found."""
    def mutator(data: dict) -> bool:
        for clip in data["clips"]:
            if clip["filename"] == filename:
                clip["youtube_video_id"] = video_id
                clip["youtube_url"] = url
                clip["youtube_upload_status"] = status
                return True
        return False
    return mutate_metadata(metadata_path, mutator)


def update_clip_duration(metadata_path: Path, filename: str, duration: float) -> bool:
    """Update a single clip's duration. Returns True if the filename was found."""
    def mutator(data: dict) -> bool:
        for clip in data["clips"]:
            if clip["filename"] == filename:
                clip["duration"] = round(duration, 4)
                return True
        return False
    return mutate_metadata(metadata_path, mutator)
