"""Compilation endpoints."""
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clipcutter.config import DIR_CLIPS, DIR_COMPILATIONS, DIR_ENCODED, DIR_KEPT, DIR_METADATA
from clipcutter.metadata import load_metadata
from clipcutter.routes._helpers import _media_type, _sanitize_filename
from clipcutter.state import AppState


class CompilationClipRef(BaseModel):
    video_stem: str
    filename: str


class CompilationRequest(BaseModel):
    clips: List[CompilationClipRef]
    transition: str = "cut"
    crossfade_duration: float = 0.5
    preset: str = "high"
    title: Optional[str] = None


def create_router(state: AppState) -> APIRouter:
    router = APIRouter()

    @router.post("/api/compilation")
    def start_compilation(req: CompilationRequest):
        if state.comp.running:
            raise HTTPException(409, "Compilation already in progress")
        if len(req.clips) < 2:
            raise HTTPException(400, "Need at least 2 clips for a compilation")

        # Resolve clip paths (prefer encoded, fall back to kept)
        clip_paths = []
        for ref in req.clips:
            enc_dir = state.output_dir / DIR_CLIPS / DIR_ENCODED / ref.video_stem
            kept_path = state.output_dir / DIR_CLIPS / DIR_KEPT / ref.video_stem / ref.filename
            found = None

            if enc_dir.exists():
                meta_path = state.output_dir / DIR_METADATA / f"{ref.video_stem}_clips.json"
                if meta_path.exists():
                    clip_metas = load_metadata(meta_path)
                    for cm in clip_metas:
                        if cm.filename == ref.filename and cm.encoded_filename:
                            enc_path = enc_dir / cm.encoded_filename
                            if enc_path.exists():
                                found = enc_path
                            break

            if found is None:
                if kept_path.exists():
                    found = kept_path
                else:
                    raise HTTPException(404, f"Clip not found: {ref.video_stem}/{ref.filename}")

            clip_paths.append(found)

        state.comp.reset()

        comp_id = f"comp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        def run():
            from clipcutter.compiler import build_compilation
            from clipcutter.audio import get_video_duration

            try:
                state.comp.update("Getting clip durations...", 10)

                durations = [get_video_duration(p) for p in clip_paths]
                total_dur = sum(durations)
                if req.transition == "crossfade":
                    total_dur -= (len(durations) - 1) * req.crossfade_duration

                state.comp.update("Building compilation...", 30)

                comp_dir = state.output_dir / DIR_CLIPS / DIR_COMPILATIONS
                comp_dir.mkdir(parents=True, exist_ok=True)

                # Build output filename
                safe_title = _sanitize_filename(req.title) if req.title else ""
                if not safe_title:
                    safe_title = comp_id
                out_name = f"{safe_title}.mp4"
                out_path = comp_dir / out_name

                build_compilation(
                    clip_paths, out_path,
                    transition=req.transition,
                    crossfade_duration=req.crossfade_duration,
                )

                state.comp.update("Saving metadata...", 90)

                # Save compilation metadata
                from clipcutter.models import CompilationMetadata
                meta = CompilationMetadata(
                    compilation_id=comp_id,
                    filename=out_name,
                    created_at=datetime.now().isoformat(timespec="seconds"),
                    clips=[
                        {"video_stem": ref.video_stem, "filename": ref.filename,
                         "duration": round(dur, 2)}
                        for ref, dur in zip(req.clips, durations)
                    ],
                    transition=req.transition,
                    crossfade_duration=req.crossfade_duration if req.transition == "crossfade" else None,
                    encoding_preset=req.preset,
                    total_duration=round(total_dur, 2),
                    status="complete",
                )

                meta_dir = state.output_dir / DIR_METADATA
                meta_dir.mkdir(parents=True, exist_ok=True)
                meta_path = meta_dir / f"{comp_id}.json"
                meta_path.write_text(
                    json.dumps(meta.to_dict(), indent=2), encoding="utf-8"
                )

                state.comp.finish(filename=out_name)

            except Exception as exc:
                state.comp.finish(error=str(exc))

        threading.Thread(target=run, daemon=True).start()
        return {"status": "started", "compilation_id": comp_id}

    @router.get("/api/compilation/status")
    def compilation_status():
        return state.comp.snapshot()

    @router.post("/api/compilation/cancel")
    def cancel_compilation():
        state.comp.cancelled = True
        return {"status": "cancelling"}

    @router.get("/api/compilations")
    def list_compilations():
        """List all completed compilations."""
        meta_dir = state.output_dir / DIR_METADATA
        comp_dir = state.output_dir / DIR_CLIPS / DIR_COMPILATIONS
        comps = []

        if not meta_dir.exists():
            return {"compilations": []}

        for meta_path in sorted(meta_dir.glob("comp_*.json")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                data.setdefault("clip_count", len(data.get("clips", [])))
                if comp_dir.exists():
                    file_exists = (comp_dir / data.get("filename", "")).exists()
                else:
                    file_exists = False
                data["file_exists"] = file_exists
                comps.append(data)
            except (json.JSONDecodeError, KeyError):
                continue

        return {"compilations": comps}

    @router.delete("/api/compilation/{compilation_id}")
    def delete_compilation(compilation_id: str):
        meta_path = state.output_dir / DIR_METADATA / f"{compilation_id}.json"
        if not meta_path.exists():
            raise HTTPException(404, "Compilation not found")

        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            video_path = state.output_dir / DIR_CLIPS / DIR_COMPILATIONS / data.get("filename", "")
            if video_path.exists():
                video_path.unlink()
        except Exception:
            pass

        meta_path.unlink()
        return {"status": "deleted"}

    @router.delete("/api/compilation/{compilation_id}/sources")
    def delete_compilation_sources(compilation_id: str):
        """Delete the individual clip files used to build a compilation."""
        meta_path = state.output_dir / DIR_METADATA / f"{compilation_id}.json"
        if not meta_path.exists():
            raise HTTPException(404, "Compilation not found")

        data = json.loads(meta_path.read_text(encoding="utf-8"))
        clips = data.get("clips", [])
        deleted = []

        for clip_ref in clips:
            video_stem = clip_ref.get("video_stem", "")
            filename = clip_ref.get("filename", "")

            # Delete kept file
            kept_path = state.output_dir / DIR_CLIPS / DIR_KEPT / video_stem / filename
            if kept_path.exists():
                try:
                    kept_path.unlink()
                    deleted.append(filename)
                except OSError:
                    pass

            # Delete encoded file via metadata
            clip_meta_path = state.output_dir / DIR_METADATA / f"{video_stem}_clips.json"
            if clip_meta_path.exists():
                for cm in load_metadata(clip_meta_path):
                    if cm.filename == filename:
                        if cm.encoded_filename:
                            enc_path = state.output_dir / DIR_CLIPS / DIR_ENCODED / video_stem / cm.encoded_filename
                            if enc_path.exists():
                                try:
                                    enc_path.unlink()
                                except OSError:
                                    pass
                        from clipcutter.metadata import update_clip_status
                        update_clip_status(clip_meta_path, filename, "deleted")
                        break

        return {"status": "deleted", "deleted_count": len(deleted), "deleted": deleted}

    @router.get("/video/compilation/{filename}")
    def serve_compilation(filename: str):
        clip_path = state.output_dir / DIR_CLIPS / DIR_COMPILATIONS / filename
        if not clip_path.exists():
            raise HTTPException(404, "Compilation not found")
        return FileResponse(clip_path, media_type=_media_type(filename))

    return router
