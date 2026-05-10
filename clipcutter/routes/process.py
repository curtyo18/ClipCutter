"""Process and source video management endpoints."""
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from clipcutter.config import DIR_CLIPS, DIR_METADATA, DIR_PENDING
from clipcutter.metadata import load_metadata, load_metadata_dict
from clipcutter.state import AppState


class ProcessRequest(BaseModel):
    folder: str
    sensitivity: float = 1.0
    context: Optional[float] = None


class FolderFileDeleteRequest(BaseModel):
    folder: str
    filename: str


def create_router(state: AppState, launch_cwd: str) -> APIRouter:
    router = APIRouter()

    @router.get("/api/defaults")
    def get_defaults():
        return {"folder": launch_cwd}

    @router.post("/api/process")
    def start_processing(req: ProcessRequest):
        if state.proc.running:
            raise HTTPException(409, "Processing already in progress")
        folder = Path(req.folder)
        if not folder.exists() or not folder.is_dir():
            raise HTTPException(400, f"Folder not found: {req.folder}")
        state.proc.reset()

        def run():
            from clipcutter.state import LogWriter
            old_stdout = sys.stdout
            sys.stdout = LogWriter(state.proc, old_stdout)
            try:
                from clipcutter import config
                from clipcutter.pipeline import process_directory
                if req.context is not None:
                    config.CLIP_CONTEXT_BEFORE_SECONDS = req.context
                    config.CLIP_CONTEXT_AFTER_SECONDS = req.context
                process_directory(
                    folder, state.output_dir,
                    sensitivity=req.sensitivity,
                    recursive=False,
                    dry_run=False,
                    overwrite=True,
                    progress=state.proc,
                )
                state.proc.finish()
            except Exception as exc:
                state.proc.finish(error=str(exc))
            finally:
                sys.stdout = old_stdout

        threading.Thread(target=run, daemon=True).start()
        return {"status": "started"}

    @router.get("/api/process/status")
    def processing_status():
        return state.proc.snapshot()

    VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.webm'}

    @router.get("/api/folder-scan")
    def scan_folder(folder: str):
        folder_path = Path(folder)
        if not folder_path.exists() or not folder_path.is_dir():
            raise HTTPException(400, f"Folder not found: {folder}")

        meta_dir = state.output_dir / DIR_METADATA
        videos = []
        now = datetime.now()

        for f in sorted(folder_path.iterdir()):
            if not f.is_file() or f.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            size_mb = round(f.stat().st_size / (1024 * 1024), 1)
            age_days = (now - datetime.fromtimestamp(f.stat().st_mtime)).days

            meta_path = meta_dir / f"{f.stem}_clips.json"
            if not meta_path.exists():
                status = "unprocessed"
            else:
                meta_dict = load_metadata_dict(meta_path)
                stored_source = Path(meta_dict.get("source_video", "")).resolve()
                if stored_source != f.resolve():
                    status = "unprocessed"
                else:
                    clips = load_metadata(meta_path)
                    if any(c.status == "pending" for c in clips):
                        status = "pending_review"
                    else:
                        status = "processed"

            videos.append({
                "filename": f.name,
                "size_mb": size_mb,
                "age_days": age_days,
                "status": status,
            })

        total_size_mb = round(sum(v["size_mb"] for v in videos), 1)
        return {"videos": videos, "total_size_mb": total_size_mb}

    @router.post("/api/folder-scan/file/delete")
    def delete_folder_file(req: FolderFileDeleteRequest):
        folder_path = Path(req.folder).resolve()
        file_path = (folder_path / req.filename).resolve()

        # Path traversal guard
        try:
            file_path.relative_to(folder_path)
        except ValueError:
            raise HTTPException(400, "Invalid filename")

        if not file_path.exists():
            raise HTTPException(404, "File not found")

        meta_path = state.output_dir / DIR_METADATA / f"{file_path.stem}_clips.json"
        if meta_path.exists():
            meta_dict = load_metadata_dict(meta_path)
            stored_source = Path(meta_dict.get("source_video", "")).resolve()
            if stored_source == file_path:
                clips = load_metadata(meta_path)
                if any(c.status == "pending" for c in clips):
                    raise HTTPException(400, "Cannot delete: some clips are still pending review")

        size_mb = round(file_path.stat().st_size / (1024 * 1024), 1)
        file_path.unlink()
        return {"status": "deleted", "freed_mb": size_mb}

    @router.get("/api/sources")
    def list_reviewed_sources():
        meta_dir = state.output_dir / DIR_METADATA
        if not meta_dir.exists():
            return {"sources": []}
        sources = []
        for meta_path in sorted(meta_dir.glob("*_clips.json")):
            data = load_metadata_dict(meta_path)
            clip_metas = load_metadata(meta_path)
            if not clip_metas:
                continue
            statuses = {c.status for c in clip_metas}
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
                "fully_reviewed": "pending" not in statuses,
                "kept": kept,
                "discarded": discarded,
                "total": len(clip_metas),
            })
        return {"sources": sources}

    @router.post("/api/sources/{video_stem}/delete")
    def delete_source(video_stem: str):
        meta_path = state.output_dir / DIR_METADATA / f"{video_stem}_clips.json"
        if not meta_path.exists():
            raise HTTPException(404, "Metadata not found")
        data = load_metadata_dict(meta_path)
        source_path = Path(data.get("source_video", ""))
        if not source_path.exists():
            raise HTTPException(404, "Source video not found (already deleted?)")
        clip_metas = load_metadata(meta_path)
        if any(c.status == "pending" for c in clip_metas):
            raise HTTPException(400, "Cannot delete: some clips are still pending review")
        size_mb = round(source_path.stat().st_size / (1024 * 1024), 1)
        source_path.unlink()
        leftover = 0
        for subdir in (DIR_PENDING, "discarded"):
            clip_dir = state.output_dir / DIR_CLIPS / subdir / video_stem
            if not clip_dir.exists():
                continue
            for f in list(clip_dir.iterdir()):
                try:
                    f.unlink()
                except OSError:
                    leftover += 1
            try:
                clip_dir.rmdir()
            except OSError:
                pass
        return {"status": "deleted", "freed_mb": size_mb, "leftover": leftover}

    return router
