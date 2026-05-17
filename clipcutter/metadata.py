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


# Fields to preserve from an existing clip when re-processing a video.
# These represent user-applied state (review, encode, upload) that should
# survive a fresh detection run. duration is handled separately because it
# is only preserved when the user has trimmed (status != "pending").
_PRESERVED_FIELDS = (
    "status",
    "custom_name",
    "encoded_filename",
    "encoding_preset",
    "youtube_video_id",
    "youtube_url",
    "youtube_upload_status",
    "highlight_regions",
)


def _merge_clip(new_clip: dict, old_clip: dict) -> dict:
    """Copy preserved user-state fields from old_clip onto new_clip.

    Returns a new dict; inputs are not mutated. Detection-derived fields
    (start_time, end_time, detection_reasons, confidence) always come from
    new_clip. duration is preserved from old_clip only if old_clip's status
    is not "pending" AND the durations differ (user-trimmed indicator)."""
    merged = dict(new_clip)
    for field_name in _PRESERVED_FIELDS:
        if field_name in old_clip:
            merged[field_name] = old_clip[field_name]
    # Conditional duration preservation: only when the user has reviewed
    # this clip (status != "pending") and actually changed the duration.
    old_status = old_clip.get("status", "pending")
    old_duration = old_clip.get("duration")
    new_duration = new_clip.get("duration")
    if (
        old_status != "pending"
        and old_duration is not None
        and old_duration != new_duration
    ):
        merged["duration"] = old_duration
    return merged


def save_metadata(clips: List[ClipMetadata], source_video: str,
                  output_dir: Path) -> Path:
    """Save clip metadata to JSON file.

    If the target file already exists, merge with it: new clips replace
    existing ones by filename, preserving user-applied state (review/encode/
    upload). Old clips whose filename is not in the new list (orphan-kept
    clips from a previous detection run) are kept as-is. This makes
    re-processing a video safe — kept clips don't lose their state and
    uploaded clips don't get silently re-uploaded.

    Writes are atomic (temp + rename) and serialized via a per-path lock
    so a concurrent update_clip_* call can't race with save_metadata.
    """
    meta_dir = output_dir / DIR_METADATA
    meta_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(source_video).stem
    meta_path = meta_dir / f"{stem}_clips.json"

    new_clip_dicts = [c.to_dict() for c in clips]

    lock = _lock_for(meta_path)
    with lock:
        if meta_path.exists():
            old = json.loads(meta_path.read_text(encoding="utf-8"))
            old_by_name = {c["filename"]: c for c in old.get("clips", [])}
            new_names = {c["filename"] for c in new_clip_dicts}

            merged_clips: list[dict] = []
            for new_clip in new_clip_dicts:
                old_clip = old_by_name.get(new_clip["filename"])
                if old_clip is not None:
                    merged_clips.append(_merge_clip(new_clip, old_clip))
                else:
                    merged_clips.append(new_clip)
            # Orphan-kept clips: in old but not in new detection run.
            # Preserve as-is so kept/encoded/uploaded clips survive.
            for old_clip in old.get("clips", []):
                if old_clip["filename"] not in new_names:
                    merged_clips.append(old_clip)
            out_clips = merged_clips
        else:
            out_clips = new_clip_dicts

        data = {
            "source_video": source_video,
            "processed_at": datetime.now().isoformat(timespec="seconds"),
            "clip_count": len(out_clips),
            "clips": out_clips,
        }

        tmp = meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(meta_path)

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
