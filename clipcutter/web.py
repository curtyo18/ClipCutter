"""Web UI for clip processing and review."""
import shutil
import threading
from pathlib import Path
from typing import Optional

import click
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from clipcutter.config import DIR_CLIPS, DIR_METADATA, DIR_PENDING
from clipcutter.metadata import load_metadata

STATIC_DIR = Path(__file__).parent / "static"


def _cleanup_stale_pending(output_dir: Path):
    """Delete files from pending/ that metadata already marks as kept/discarded.

    Also sweeps the ``.waveform.json`` sidecars sitting next to each stale clip
    — leaving them behind would block ``video_dir.rmdir()`` (the dir would no
    longer be empty) and they'd accumulate forever across runs.
    """
    pending_dir = output_dir / DIR_CLIPS / DIR_PENDING
    meta_dir = output_dir / DIR_METADATA
    if not pending_dir.exists():
        return

    removed = 0
    for video_dir in list(pending_dir.iterdir()):
        if not video_dir.is_dir():
            continue
        meta_path = meta_dir / f"{video_dir.name}_clips.json"
        if not meta_path.exists():
            continue

        clip_metas = load_metadata(meta_path)
        # Target both the clip itself and its waveform sidecar, but only for
        # clips that are no longer pending. Clips still in "pending" status
        # keep their sidecar so the review UI doesn't have to re-derive it.
        non_pending = set()
        for c in clip_metas:
            if c.status != "pending":
                non_pending.add(c.filename)
                non_pending.add(c.filename + ".waveform.json")

        for f in list(video_dir.iterdir()):
            if f.name in non_pending:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass

        if video_dir.exists() and not any(video_dir.iterdir()):
            video_dir.rmdir()

    if removed:
        click.echo(f"Cleaned up {removed} stale file(s) from pending.")


def create_app(output_dir: Path, cwd: Optional[str] = None) -> FastAPI:
    """Create a FastAPI app for processing and reviewing clips."""
    from clipcutter.state import AppState
    from clipcutter.routes import process, review, encode, compile, youtube

    output_dir = Path(output_dir).resolve()
    state = AppState(output_dir)
    launch_cwd = cwd or str(Path.cwd())

    app = FastAPI(title="ClipCutter")

    @app.on_event("startup")
    def _kick_off_cleanup():
        """Run stale-pending cleanup on a daemon thread so a large pending/
        tree doesn't block uvicorn from accepting requests at startup."""
        t = threading.Thread(
            target=_cleanup_stale_pending,
            args=(output_dir,),
            name="clipcutter-startup-cleanup",
            daemon=True,
        )
        t.start()

    app.include_router(process.create_router(state, launch_cwd))
    app.include_router(compile.create_router(state))
    app.include_router(review.create_router(state))
    app.include_router(encode.create_router(state))
    app.include_router(youtube.create_router(state))

    dist_dir = STATIC_DIR / "dist"
    if dist_dir.exists():
        app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def index():
        dist_path = STATIC_DIR / "dist" / "index.html"
        if dist_path.exists():
            return dist_path.read_text(encoding="utf-8")
        # Fallback for dev: serve old index.html if dist not built yet
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    return app
