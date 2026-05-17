"""Encoding endpoints."""
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from clipcutter.config import DIR_CLIPS, DIR_COMPILATIONS, DIR_ENCODED, DIR_KEPT, DIR_METADATA
from clipcutter.encoder import FFMPEG_ENCODE_TIMEOUT
from clipcutter.errors import FFmpegTimeoutError
from clipcutter.metadata import (
    load_metadata, load_metadata_dict, update_clip_encoding,
    update_clip_status, clear_clip_encoding,
)
from clipcutter.routes._helpers import _safe_join, _sanitize_filename
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
                    "video_url": f"/video/kept/{video_stem}/{clip.filename}",
                    "clipped_at": clipped_at,
                    "custom_name": clip.custom_name,
                    "encoded_filename": clip.encoded_filename,
                    "encoding_preset": clip.encoding_preset,
                    "youtube_video_id": clip.youtube_video_id,
                    "youtube_url": clip.youtube_url,
                    "youtube_upload_status": clip.youtube_upload_status,
                }

                # File size of kept clip
                clip_info["size_mb"] = round(clip_path.stat().st_size / (1024 * 1024), 1)

                # Check if encoded file actually exists
                if clip.encoded_filename:
                    enc_path = state.output_dir / DIR_CLIPS / DIR_ENCODED / video_stem / clip.encoded_filename
                    clip_info["encoded_exists"] = enc_path.exists()
                    if enc_path.exists():
                        clip_info["encoded_video_url"] = f"/video/encoded/{video_stem}/{clip.encoded_filename}"
                        clip_info["encoded_size_mb"] = round(enc_path.stat().st_size / (1024 * 1024), 1)
                    else:
                        clip_info["encoded_size_mb"] = None
                else:
                    clip_info["encoded_exists"] = False
                    clip_info["encoded_size_mb"] = None

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
        kept_base = state.output_dir / DIR_CLIPS / DIR_KEPT
        encoded_base = state.output_dir / DIR_CLIPS / DIR_ENCODED
        meta_base = state.output_dir / DIR_METADATA

        # Validate all files exist before starting (also rejects traversal)
        for clip_ref in req.clips:
            kept_path = _safe_join(kept_base, clip_ref.video_stem, clip_ref.filename)
            if not kept_path.exists():
                raise HTTPException(404, f"Clip not found: {clip_ref.video_stem}/{clip_ref.filename}")

        state.enc.reset(total=len(req.clips))

        def run():
            from clipcutter.encoder import build_encode_command

            for i, clip_ref in enumerate(req.clips):
                if state.enc.cancel_event.is_set():
                    break

                state.enc.set_current(clip_ref.filename, i + 1)
                input_path = _safe_join(kept_base, clip_ref.video_stem, clip_ref.filename)
                encoded_dir = _safe_join(encoded_base, clip_ref.video_stem)
                meta_path = _safe_join(meta_base, f"{clip_ref.video_stem}_clips.json")

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
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    cmd = build_encode_command(
                        input_path, output_path, preset,
                        req.target_fps, req.slowdown_factor,
                    )

                    if cmd is None:
                        # Copy preset, no fps/slowdown — straight file copy.
                        # No Popen to register; cancel between iterations is
                        # sufficient for the no-op case.
                        shutil.copy2(str(input_path), str(output_path))
                    else:
                        proc = subprocess.Popen(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        state.enc.set_popen(proc)
                        try:
                            # Flat ceiling matches encoder.FFMPEG_ENCODE_TIMEOUT.
                            # A hung encode can't pin the worker forever, even
                            # if the user never hits cancel. On timeout the
                            # error is caught per-clip below so the batch can
                            # advance to the next file.
                            try:
                                _stdout, stderr = proc.communicate(
                                    timeout=FFMPEG_ENCODE_TIMEOUT,
                                )
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                try:
                                    _stdout, stderr = proc.communicate(timeout=5)
                                except Exception:
                                    stderr = ""
                                if output_path.exists():
                                    try:
                                        output_path.unlink()
                                    except OSError:
                                        pass
                                raise FFmpegTimeoutError(
                                    f"FFmpeg encoding timed out after "
                                    f"{FFMPEG_ENCODE_TIMEOUT}s for {clip_ref.filename}"
                                )
                        finally:
                            state.enc.set_popen(None)

                        if proc.returncode != 0:
                            # Cancellation surfaces as a non-zero return code
                            # (SIGTERM/SIGKILL). Treat it as cancellation
                            # rather than per-clip error if the event is set.
                            if state.enc.cancel_event.is_set():
                                if output_path.exists():
                                    try:
                                        output_path.unlink()
                                    except OSError:
                                        pass
                                break
                            if output_path.exists():
                                try:
                                    output_path.unlink()
                                except OSError:
                                    pass
                            stderr_tail = (stderr or "")[-500:]
                            raise RuntimeError(
                                f"FFmpeg encoding failed for {clip_ref.filename}: {stderr_tail}"
                            )

                    if meta_path.exists():
                        if not update_clip_encoding(meta_path, clip_ref.filename, out_name, req.preset):
                            logger.warning(
                                "update_clip_encoding: no match for %s in %s",
                                clip_ref.filename, meta_path,
                            )
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
        state.enc.cancel_event.set()
        # Terminate the in-flight ffmpeg subprocess if one is registered.
        # TOCTOU: the worker may clear popen between get_popen() and
        # terminate(), so wrap each call in try/except.
        proc = state.enc.get_popen()
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
            except Exception:
                pass
        return {"status": "cancelling"}

    @router.delete("/api/kept/{video_stem}/{filename}")
    def delete_kept_clip(video_stem: str, filename: str):
        """Delete a kept clip file and mark it as discarded in metadata."""
        kept_base = state.output_dir / DIR_CLIPS / DIR_KEPT
        meta_base = state.output_dir / DIR_METADATA
        kept_path = _safe_join(kept_base, video_stem, filename)
        meta_path = _safe_join(meta_base, f"{video_stem}_clips.json")

        if not kept_path.exists():
            raise HTTPException(404, "Clip not found")

        kept_path.unlink()

        # Remove the parent folder if it's now empty
        if kept_path.parent.is_dir() and not any(kept_path.parent.iterdir()):
            kept_path.parent.rmdir()

        if meta_path.exists():
            if not update_clip_status(meta_path, filename, "discarded"):
                raise HTTPException(404, "Clip not found in metadata")

        return {"status": "deleted"}

    @router.get("/api/open-folder/kept/{video_stem}")
    def open_folder(video_stem: str):
        kept_base = state.output_dir / DIR_CLIPS / DIR_KEPT
        folder = _safe_join(kept_base, video_stem)
        if not folder.exists():
            raise HTTPException(404, "Folder not found")

        if sys.platform == "win32":
            os.startfile(str(folder))
        elif sys.platform == "darwin":
            try:
                subprocess.Popen(
                    ["open", str(folder)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                raise HTTPException(501, "Folder-open not available on this platform")
            except OSError as exc:
                raise HTTPException(501, f"Folder-open failed: {exc}")
        else:
            try:
                subprocess.Popen(
                    ["xdg-open", str(folder)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                raise HTTPException(501, "xdg-open not installed")
            except OSError as exc:
                raise HTTPException(501, f"Folder-open failed: {exc}")
        return {"status": "opened"}

    @router.get("/api/storage-summary")
    def storage_summary():
        def _scan(path: Path) -> tuple[int, float]:
            count = 0
            size_mb = 0.0
            if path.exists():
                for f in path.rglob("*"):
                    if f.is_file() and not f.name.startswith(".") and f.suffix != ".json":
                        count += 1
                        size_mb += f.stat().st_size / (1024 * 1024)
            return count, round(size_mb, 1)

        kept_count, kept_mb = _scan(state.output_dir / DIR_CLIPS / DIR_KEPT)
        enc_count, enc_mb = _scan(state.output_dir / DIR_CLIPS / DIR_ENCODED)
        comp_count, comp_mb = _scan(state.output_dir / DIR_CLIPS / DIR_COMPILATIONS)

        return {
            "kept": {"count": kept_count, "size_mb": kept_mb},
            "encoded": {"count": enc_count, "size_mb": enc_mb},
            "compilations": {"count": comp_count, "size_mb": comp_mb},
            "total_mb": round(kept_mb + enc_mb + comp_mb, 1),
        }

    @router.delete("/api/encoded/{video_stem}/{filename}")
    def delete_encoded_clip(video_stem: str, filename: str):
        """Delete the encoded version of a kept clip and clear its encoding metadata."""
        meta_base = state.output_dir / DIR_METADATA
        encoded_base = state.output_dir / DIR_CLIPS / DIR_ENCODED
        meta_path = _safe_join(meta_base, f"{video_stem}_clips.json")
        if not meta_path.exists():
            raise HTTPException(404, "Clip metadata not found")

        clip_metas = load_metadata(meta_path)
        encoded_filename = None
        for clip in clip_metas:
            if clip.filename == filename and clip.encoded_filename:
                encoded_filename = clip.encoded_filename
                break

        if not encoded_filename:
            raise HTTPException(404, "No encoded version found")

        enc_path = _safe_join(encoded_base, video_stem, encoded_filename)
        freed_mb = 0.0
        if enc_path.exists():
            freed_mb = round(enc_path.stat().st_size / (1024 * 1024), 1)
            enc_path.unlink()
            if enc_path.parent.is_dir() and not any(enc_path.parent.iterdir()):
                enc_path.parent.rmdir()

        if not clear_clip_encoding(meta_path, filename):
            logger.warning(
                "clear_clip_encoding: no match for %s in %s",
                filename, meta_path,
            )
        return {"status": "deleted", "freed_mb": freed_mb}

    return router
