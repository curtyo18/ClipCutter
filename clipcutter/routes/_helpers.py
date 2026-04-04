"""Shared helper utilities for route handlers."""
from pathlib import Path


def _sanitize_filename(name: str) -> str:
    """Strip unsafe chars, replace spaces with underscores."""
    safe = "".join(c for c in name if c.isalnum() or c in " _-").strip()
    return safe.replace(" ", "_")


def _media_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {"webm": "video/webm", ".gif": "image/gif"}.get(ext, "video/mp4")
