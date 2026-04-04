"""Web UI for clip processing and review."""
import shutil
from pathlib import Path
from typing import Optional

import click
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from clipcutter.config import DIR_CLIPS, DIR_METADATA, DIR_PENDING
from clipcutter.metadata import load_metadata

STATIC_DIR = Path(__file__).parent / "static"


def _cleanup_stale_pending(output_dir: Path):
    """Delete files from pending/ that metadata already marks as kept/discarded."""
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
        non_pending = {c.filename for c in clip_metas if c.status != "pending"}

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

    _cleanup_stale_pending(output_dir)

    app = FastAPI(title="ClipCutter")

    app.include_router(process.create_router(state, launch_cwd))
    app.include_router(review.create_router(state))
    app.include_router(encode.create_router(state))
    app.include_router(compile.create_router(state))
    app.include_router(youtube.create_router(state))

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    return app
