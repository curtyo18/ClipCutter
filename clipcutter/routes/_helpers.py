"""Shared helper utilities for route handlers."""
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import HTTPException


def _sanitize_filename(name: str) -> str:
    """Strip unsafe chars, replace spaces with underscores."""
    safe = "".join(c for c in name if c.isalnum() or c in " _-").strip()
    return safe.replace(" ", "_")


def _media_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {"webm": "video/webm", ".gif": "image/gif"}.get(ext, "video/mp4")


def _safe_join(base: Path, *parts: str) -> Path:
    """Join `parts` onto `base` and return the resolved path, refusing escapes.

    Rejects (HTTP 400) any input that:
      - Contains a NUL byte
      - Has a part that is empty, absolute, or contains a `..` segment
        (using both POSIX and Windows separator semantics, since users may
        come from either OS via a browser)
      - Resolves to a path outside `base.resolve()`

    Returns the resolved Path on success. The base itself is always allowed.
    """
    base_resolved = base.resolve()

    for part in parts:
        if part is None or part == "":
            raise HTTPException(400, "Invalid path")
        if "\x00" in part:
            raise HTTPException(400, "Invalid path")
        # Reject parts that are absolute under either OS's rules. A part
        # like "/etc/passwd" or "C:\\Windows" would anchor the join to a
        # filesystem root.
        if PurePosixPath(part).is_absolute() or PureWindowsPath(part).is_absolute():
            raise HTTPException(400, "Invalid path")
        # Reject any `..` segment under either separator convention.
        segments = (
            PurePosixPath(part).parts + PureWindowsPath(part).parts
        )
        if any(seg == ".." for seg in segments):
            raise HTTPException(400, "Invalid path")

    joined = base.joinpath(*parts).resolve()
    try:
        joined.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(400, "Invalid path")
    return joined
