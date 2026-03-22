"""Web UI for clip processing and review."""

import shutil
import sys
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from clipcutter.config import DIR_CLIPS, DIR_KEPT, DIR_METADATA, DIR_PENDING
from clipcutter.metadata import load_metadata, load_metadata_dict, update_clip_status

STATIC_DIR = Path(__file__).parent / "static"


class ProcessRequest(BaseModel):
    folder: str
    sensitivity: float = 1.0
    context: Optional[float] = None


class ProcessingState:
    """Thread-safe processing state shared between API endpoints."""

    def __init__(self):
        self.running = False
        self.log_lines: list[str] = []
        self.error: Optional[str] = None
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self.running = True
            self.log_lines = []
            self.error = None

    def add_line(self, line: str):
        with self._lock:
            self.log_lines.append(line)

    def finish(self, error: Optional[str] = None):
        with self._lock:
            self.running = False
            self.error = error

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "log": list(self.log_lines),
                "error": self.error,
            }


class LogWriter:
    """Captures writes to stdout and stores them in ProcessingState."""

    def __init__(self, state: ProcessingState, original):
        self.state = state
        self.original = original
        self._buf = ""

    def write(self, text: str):
        self.original.write(text)
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            stripped = line.strip()
            if stripped:
                self.state.add_line(stripped)

    def flush(self):
        self.original.flush()
        if self._buf.strip():
            self.state.add_line(self._buf.strip())
            self._buf = ""


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
        import click
        click.echo(f"Cleaned up {removed} stale file(s) from pending.")


def create_app(output_dir: Path, cwd: Optional[str] = None) -> FastAPI:
    """Create a FastAPI app for processing and reviewing clips."""
    output_dir = Path(output_dir).resolve()
    app = FastAPI(title="ClipCutter")
    proc_state = ProcessingState()
    launch_cwd = cwd or str(Path.cwd())

    # Clean up stale files in pending that have already been kept/discarded
    _cleanup_stale_pending(output_dir)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Processing API
    # ------------------------------------------------------------------

    @app.get("/api/defaults")
    def get_defaults():
        return {"folder": launch_cwd}

    @app.post("/api/process")
    def start_processing(req: ProcessRequest):
        if proc_state.running:
            raise HTTPException(409, "Processing already in progress")

        folder = Path(req.folder)
        if not folder.exists() or not folder.is_dir():
            raise HTTPException(400, f"Folder not found: {req.folder}")

        proc_state.reset()

        def run():
            old_stdout = sys.stdout
            sys.stdout = LogWriter(proc_state, old_stdout)
            try:
                from clipcutter import config
                from clipcutter.pipeline import process_directory

                if req.context is not None:
                    config.CLIP_CONTEXT_BEFORE_SECONDS = req.context
                    config.CLIP_CONTEXT_AFTER_SECONDS = req.context

                process_directory(
                    folder, output_dir,
                    sensitivity=req.sensitivity,
                    recursive=False,
                    dry_run=False,
                    overwrite=True,
                )
                proc_state.finish()
            except Exception as exc:
                proc_state.finish(error=str(exc))
            finally:
                sys.stdout = old_stdout

        threading.Thread(target=run, daemon=True).start()
        return {"status": "started"}

    @app.get("/api/process/status")
    def processing_status():
        return proc_state.snapshot()

    # ------------------------------------------------------------------
    # Review API
    # ------------------------------------------------------------------

    @app.get("/api/clips")
    def list_clips():
        pending_dir = output_dir / DIR_CLIPS / DIR_PENDING
        meta_dir = output_dir / DIR_METADATA

        if not pending_dir.exists():
            return {"clips": [], "total": 0}

        clips = []
        for video_dir in sorted(pending_dir.iterdir()):
            if not video_dir.is_dir():
                continue

            video_stem = video_dir.name
            meta_path = meta_dir / f"{video_stem}_clips.json"
            if not meta_path.exists():
                continue

            meta_data = load_metadata_dict(meta_path)
            source_video = meta_data.get("source_video", video_stem)
            clip_metas = load_metadata(meta_path)

            for clip in clip_metas:
                if clip.status != "pending":
                    continue
                clip_path = video_dir / clip.filename
                if not clip_path.exists():
                    continue

                clips.append({
                    "filename": clip.filename,
                    "source_video": source_video,
                    "video_stem": video_stem,
                    "start_time": clip.start_time,
                    "end_time": clip.end_time,
                    "duration": clip.duration,
                    "detection_reasons": clip.detection_reasons,
                    "confidence": clip.confidence,
                    "video_url": f"/video/{video_stem}/{clip.filename}",
                })

        clips.sort(key=lambda c: -c["confidence"])
        return {"clips": clips, "total": len(clips)}

    @app.get("/video/{video_stem}/{filename}")
    def serve_video(video_stem: str, filename: str):
        clip_path = output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        if not clip_path.exists():
            clip_path = output_dir / DIR_CLIPS / DIR_KEPT / video_stem / filename
        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")
        return FileResponse(clip_path, media_type="video/mp4")

    @app.post("/api/clips/{video_stem}/{filename}/keep")
    def keep_clip(video_stem: str, filename: str):
        clip_path = output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        meta_path = output_dir / DIR_METADATA / f"{video_stem}_clips.json"

        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")

        kept_dir = output_dir / DIR_CLIPS / DIR_KEPT / video_stem
        kept_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(clip_path), str(kept_dir / filename))
        except OSError:
            pass
        update_clip_status(meta_path, filename, "kept")
        return {"status": "kept"}

    @app.post("/api/clips/{video_stem}/{filename}/discard")
    def discard_clip(video_stem: str, filename: str):
        clip_path = output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        meta_path = output_dir / DIR_METADATA / f"{video_stem}_clips.json"

        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")

        update_clip_status(meta_path, filename, "discarded")
        return {"status": "discarded"}

    # ------------------------------------------------------------------
    # Source video cleanup API
    # ------------------------------------------------------------------

    @app.get("/api/sources")
    def list_reviewed_sources():
        """List source videos where all clips have been reviewed."""
        meta_dir = output_dir / DIR_METADATA
        if not meta_dir.exists():
            return {"sources": []}

        sources = []
        for meta_path in sorted(meta_dir.glob("*_clips.json")):
            data = load_metadata_dict(meta_path)
            clip_metas = load_metadata(meta_path)
            if not clip_metas:
                continue

            statuses = {c.status for c in clip_metas}
            has_pending = "pending" in statuses
            kept = sum(1 for c in clip_metas if c.status == "kept")
            discarded = sum(1 for c in clip_metas if c.status == "discarded")

            source_path = Path(data.get("source_video", ""))
            exists = source_path.exists()
            size_mb = round(source_path.stat().st_size / (1024 * 1024), 1) if exists else 0

            sources.append({
                "video_stem": meta_path.stem.replace("_clips", ""),
                "source_path": str(source_path),
                "exists": exists,
                "size_mb": size_mb,
                "fully_reviewed": not has_pending,
                "kept": kept,
                "discarded": discarded,
                "total": len(clip_metas),
            })

        return {"sources": sources}

    @app.post("/api/sources/{video_stem}/delete")
    def delete_source(video_stem: str):
        meta_path = output_dir / DIR_METADATA / f"{video_stem}_clips.json"
        if not meta_path.exists():
            raise HTTPException(404, "Metadata not found")

        data = load_metadata_dict(meta_path)
        source_path = Path(data.get("source_video", ""))

        if not source_path.exists():
            raise HTTPException(404, "Source video not found (already deleted?)")

        # Safety: only delete if all clips are reviewed
        clip_metas = load_metadata(meta_path)
        if any(c.status == "pending" for c in clip_metas):
            raise HTTPException(400, "Cannot delete: some clips are still pending review")

        size_mb = round(source_path.stat().st_size / (1024 * 1024), 1)
        source_path.unlink()

        # Clean up pending and discarded clip files for this video
        leftover = 0
        for subdir in (DIR_PENDING, "discarded"):
            clip_dir = output_dir / DIR_CLIPS / subdir / video_stem
            if not clip_dir.exists():
                continue
            for f in list(clip_dir.iterdir()):
                try:
                    f.unlink()
                except OSError:
                    leftover += 1
            # Remove dir if empty
            try:
                clip_dir.rmdir()
            except OSError:
                pass

        return {"status": "deleted", "freed_mb": size_mb, "leftover": leftover}

    return app
