"""Encoding endpoints."""
import os
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clipcutter.config import DIR_CLIPS, DIR_ENCODED, DIR_KEPT, DIR_METADATA
from clipcutter.metadata import load_metadata, load_metadata_dict, update_clip_encoding, update_clip_status
from clipcutter.routes._helpers import _media_type, _sanitize_filename
from clipcutter.state import AppState


class EncodeClipRef(BaseModel):
    video_stem: str
    filename: str


class EncodeRequest(BaseModel):
    clips: List[EncodeClipRef]
    preset: str
    target_fps: Optional[int] = None
    slowdown_factor: Optional[float] = None  # For GIF: 0.5 = half speed


def create_router(state: AppState) -> APIRouter:
    router = APIRouter()

    @router.get("/api/encoding/presets")
    def list_encoding_presets():
        from clipcutter.encoder import get_presets
        from clipcutter.config import DEFAULT_ENCODING_PRESET
        presets = get_presets()
        result = []
        for name, p in presets.items():
            result.append({
                "name": name,
                "display_name": p.display_name,
                "extension": p.extension or "(same as source)",
            })
        return {
            "presets": result,
            "default": DEFAULT_ENCODING_PRESET,
            "fps_options": [None, 24, 30, 60],
        }

    @router.get("/api/kept")
    def list_kept_clips():
        """List all kept clips with encoding and YouTube status from metadata."""
        kept_dir = state.output_dir / DIR_CLIPS / DIR_KEPT
        meta_dir = state.output_dir / DIR_METADATA

        if not kept_dir.exists():
            return {"clips": [], "total": 0}

        clips = []
        for video_dir in sorted(kept_dir.iterdir()):
            if not video_dir.is_dir():
                continue

            video_stem = video_dir.name
            meta_path = meta_dir / f"{video_stem}_clips.json"
            if not meta_path.exists():
                continue

            meta_data = load_metadata_dict(meta_path)
            source_video = meta_data.get("source_video", video_stem)
            clipped_at = meta_data.get("processed_at", "")
            clip_metas = load_metadata(meta_path)

            for clip in clip_metas:
                if clip.status != "kept":
                    continue
                clip_path = video_dir / clip.filename
                if not clip_path.exists():
                    continue

                clip_info = {
                    "filename": clip.filename,
                    "source_video": source_video,
                    "video_stem": video_stem,
                    "start_time": clip.start_time,
                    "end_time": clip.end_time,
                    "duration": clip.duration,
                    "detection_reasons": clip.detection_reasons,
                    "confidence": clip.confidence,
                    "video_url": f"/video/{video_stem}/{clip.filename}",
                    "clipped_at": clipped_at,
                    "custom_name": clip.custom_name,
                    "encoded_filename": clip.encoded_filename,
                    "encoding_preset": clip.encoding_preset,
                    "youtube_video_id": clip.youtube_video_id,
                    "youtube_url": clip.youtube_url,
                    "youtube_upload_status": clip.youtube_upload_status,
                }

                # Check if encoded file actually exists
                if clip.encoded_filename:
                    enc_path = state.output_dir / DIR_CLIPS / DIR_ENCODED / video_stem / clip.encoded_filename
                    clip_info["encoded_exists"] = enc_path.exists()
                    if enc_path.exists():
                        clip_info["encoded_video_url"] = f"/video/encoded/{video_stem}/{clip.encoded_filename}"
                else:
                    clip_info["encoded_exists"] = False

                clips.append(clip_info)

        clips.sort(key=lambda c: c.get("clipped_at", ""), reverse=True)
        return {"clips": clips, "total": len(clips)}

    @router.post("/api/encode")
    def start_encoding(req: EncodeRequest):
        if state.enc.running:
            raise HTTPException(409, "Encoding already in progress")

        if not req.clips:
            raise HTTPException(400, "No clips selected for encoding")

        from clipcutter.encoder import get_presets
        presets = get_presets()
        if req.preset not in presets:
            raise HTTPException(400, f"Unknown preset: {req.preset}")

        preset = presets[req.preset]

        # Validate all files exist before starting
        for clip_ref in req.clips:
            kept_path = state.output_dir / DIR_CLIPS / DIR_KEPT / clip_ref.video_stem / clip_ref.filename
            if not kept_path.exists():
                raise HTTPException(404, f"Clip not found: {clip_ref.video_stem}/{clip_ref.filename}")

        state.enc.reset(total=len(req.clips))

        def run():
            from clipcutter.encoder import encode_clip

            for i, clip_ref in enumerate(req.clips):
                if state.enc.cancelled:
                    break

                state.enc.set_current(clip_ref.filename, i + 1)
                input_path = state.output_dir / DIR_CLIPS / DIR_KEPT / clip_ref.video_stem / clip_ref.filename
                encoded_dir = state.output_dir / DIR_CLIPS / DIR_ENCODED / clip_ref.video_stem
                meta_path = state.output_dir / DIR_METADATA / f"{clip_ref.video_stem}_clips.json"

                # Use custom_name from metadata if available for output filename
                custom_stem = None
                if meta_path.exists():
                    clip_metas_local = load_metadata(meta_path)
                    for cm in clip_metas_local:
                        if cm.filename == clip_ref.filename and cm.custom_name:
                            sanitized = _sanitize_filename(cm.custom_name)
                            if sanitized:
                                custom_stem = sanitized
                            break

                stem = custom_stem or Path(clip_ref.filename).stem
                ext = preset.extension if preset.extension else Path(clip_ref.filename).suffix
                out_name = f"{stem}.{req.preset}{ext}"
                output_path = encoded_dir / out_name

                try:
                    encode_clip(input_path, output_path, preset, req.target_fps, req.slowdown_factor)
                    if meta_path.exists():
                        update_clip_encoding(meta_path, clip_ref.filename, out_name, req.preset)
                    state.enc.add_completed(clip_ref.filename)
                except Exception as exc:
                    state.enc.add_error(clip_ref.filename, str(exc))

            state.enc.finish()

        threading.Thread(target=run, daemon=True).start()
        return {"status": "started"}

    @router.get("/api/encode/status")
    def encoding_status():
        return state.enc.snapshot()

    @router.post("/api/encode/cancel")
    def cancel_encoding():
        state.enc.cancelled = True
        return {"status": "cancelling"}

    @router.get("/video/encoded/{video_stem}/{filename}")
    def serve_encoded_video(video_stem: str, filename: str):
        clip_path = state.output_dir / DIR_CLIPS / DIR_ENCODED / video_stem / filename
        if not clip_path.exists():
            raise HTTPException(404, "Encoded clip not found")
        return FileResponse(clip_path, media_type=_media_type(filename))

    @router.delete("/api/kept/{video_stem}/{filename}")
    def delete_kept_clip(video_stem: str, filename: str):
        """Delete a kept clip file and mark it as discarded in metadata."""
        kept_path = state.output_dir / DIR_CLIPS / DIR_KEPT / video_stem / filename
        meta_path = state.output_dir / DIR_METADATA / f"{video_stem}_clips.json"

        if not kept_path.exists():
            raise HTTPException(404, "Clip not found")

        kept_path.unlink()

        if meta_path.exists():
            update_clip_status(meta_path, filename, "discarded")

        return {"status": "deleted"}

    @router.get("/api/open-folder/kept/{video_stem}")
    def open_folder(video_stem: str):
        folder = state.output_dir / DIR_CLIPS / DIR_KEPT / video_stem
        if not folder.exists():
            raise HTTPException(404, "Folder not found")
        os.startfile(str(folder))
        return {"status": "opened"}

    return router
