"""Web UI for clip processing and review."""

import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from clipcutter.config import (
    DIR_CLIPS, DIR_COMPILATIONS, DIR_ENCODED, DIR_KEPT, DIR_METADATA, DIR_PENDING,
    YOUTUBE_CREDENTIALS_FILE, YOUTUBE_DEFAULT_CATEGORY, YOUTUBE_DEFAULT_PRIVACY,
)
from clipcutter.metadata import (
    load_metadata, load_metadata_dict, update_clip_encoding,
    update_clip_status, update_clip_youtube,
)

STATIC_DIR = Path(__file__).parent / "static"


class ProcessRequest(BaseModel):
    folder: str
    sensitivity: float = 1.0
    context: Optional[float] = None


class KeepRequest(BaseModel):
    trim_start: float = 0.0
    trim_end: float = 0.0
    needs_trim: bool = False
    custom_name: Optional[str] = None


class EncodeClipRef(BaseModel):
    video_stem: str
    filename: str


class EncodeRequest(BaseModel):
    clips: List[EncodeClipRef]
    preset: str
    target_fps: Optional[int] = None
    slowdown_factor: Optional[float] = None  # For GIF: 0.5 = half speed


class YouTubeAuthStartRequest(BaseModel):
    client_id: str
    client_secret: str


class YouTubeUploadRequest(BaseModel):
    video_stem: str
    filename: str
    use_encoded: bool = True
    title: str
    description: str = ""
    tags: List[str] = []
    category_id: str = YOUTUBE_DEFAULT_CATEGORY
    privacy: str = YOUTUBE_DEFAULT_PRIVACY
    playlist_id: Optional[str] = None


class YouTubeBatchUploadRequest(BaseModel):
    clips: List[YouTubeUploadRequest]


class PlaylistCreateRequest(BaseModel):
    title: str
    privacy: str = "private"


class CompilationClipRef(BaseModel):
    video_stem: str
    filename: str


class CompilationRequest(BaseModel):
    clips: List[CompilationClipRef]
    transition: str = "cut"
    crossfade_duration: float = 0.5
    preset: str = "high"
    title: Optional[str] = None


class CompilationState:
    """Thread-safe compilation build state."""

    def __init__(self):
        self.running = False
        self.current_step: str = ""
        self.progress_pct: float = 0
        self.completed = False
        self.error: Optional[str] = None
        self.output_filename: Optional[str] = None
        self.cancelled = False
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self.running = True
            self.current_step = "Starting..."
            self.progress_pct = 0
            self.completed = False
            self.error = None
            self.output_filename = None
            self.cancelled = False

    def update(self, step: str, pct: float):
        with self._lock:
            self.current_step = step
            self.progress_pct = pct

    def finish(self, filename: Optional[str] = None, error: Optional[str] = None):
        with self._lock:
            self.running = False
            self.completed = error is None
            self.error = error
            self.output_filename = filename

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "current_step": self.current_step,
                "progress_pct": self.progress_pct,
                "completed": self.completed,
                "error": self.error,
                "output_filename": self.output_filename,
                "cancelled": self.cancelled,
            }


class EncodingState:
    """Thread-safe encoding state shared between API endpoints."""

    def __init__(self):
        self.running = False
        self.current_file: Optional[str] = None
        self.current_index: int = 0
        self.total: int = 0
        self.completed: list[str] = []
        self.errors: list[dict] = []
        self.cancelled = False
        self._lock = threading.Lock()

    def reset(self, total: int):
        with self._lock:
            self.running = True
            self.current_file = None
            self.current_index = 0
            self.total = total
            self.completed = []
            self.errors = []
            self.cancelled = False

    def set_current(self, filename: str, index: int):
        with self._lock:
            self.current_file = filename
            self.current_index = index

    def add_completed(self, filename: str):
        with self._lock:
            self.completed.append(filename)

    def add_error(self, filename: str, error: str):
        with self._lock:
            self.errors.append({"filename": filename, "error": error})

    def finish(self):
        with self._lock:
            self.running = False
            self.current_file = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "current_file": self.current_file,
                "current_index": self.current_index,
                "total": self.total,
                "completed": list(self.completed),
                "errors": list(self.errors),
                "cancelled": self.cancelled,
            }


class UploadState:
    """Thread-safe upload state shared between API endpoints."""

    def __init__(self):
        self.running = False
        self.current_file: Optional[str] = None
        self.current_index: int = 0
        self.total: int = 0
        self.bytes_sent: int = 0
        self.bytes_total: int = 0
        self.completed: list[dict] = []
        self.errors: list[dict] = []
        self.cancelled = False
        self._lock = threading.Lock()

    def reset(self, total: int):
        with self._lock:
            self.running = True
            self.current_file = None
            self.current_index = 0
            self.total = total
            self.bytes_sent = 0
            self.bytes_total = 0
            self.completed = []
            self.errors = []
            self.cancelled = False

    def set_current(self, filename: str, index: int):
        with self._lock:
            self.current_file = filename
            self.current_index = index
            self.bytes_sent = 0
            self.bytes_total = 0

    def update_progress(self, bytes_sent: int, bytes_total: int):
        with self._lock:
            self.bytes_sent = bytes_sent
            self.bytes_total = bytes_total

    def add_completed(self, filename: str, video_id: str, url: str):
        with self._lock:
            self.completed.append({
                "filename": filename,
                "video_id": video_id,
                "url": url,
            })

    def add_error(self, filename: str, error: str):
        with self._lock:
            self.errors.append({"filename": filename, "error": error})

    def finish(self):
        with self._lock:
            self.running = False
            self.current_file = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "current_file": self.current_file,
                "current_index": self.current_index,
                "total": self.total,
                "bytes_sent": self.bytes_sent,
                "bytes_total": self.bytes_total,
                "completed": list(self.completed),
                "errors": list(self.errors),
                "cancelled": self.cancelled,
            }


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
    enc_state = EncodingState()
    upl_state = UploadState()
    comp_state = CompilationState()
    def _sanitize_filename(name: str) -> str:
        """Strip unsafe chars, replace spaces with underscores."""
        safe = "".join(c for c in name if c.isalnum() or c in " _-").strip()
        return safe.replace(" ", "_")

    def _media_type(filename: str) -> str:
        ext = Path(filename).suffix.lower()
        return {"webm": "video/webm", ".gif": "image/gif"}.get(ext, "video/mp4")

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
                    "highlight_regions": clip.highlight_regions or [],
                })

        clips.sort(key=lambda c: -c["confidence"])
        return {"clips": clips, "total": len(clips)}

    @app.get("/video/{video_stem}/{filename}")
    def serve_video(video_stem: str, filename: str):
        clip_path = output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        if not clip_path.exists():
            clip_path = output_dir / DIR_CLIPS / DIR_KEPT / video_stem / filename
        if not clip_path.exists():
            clip_path = output_dir / DIR_CLIPS / DIR_ENCODED / video_stem / filename
        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")
        return FileResponse(clip_path, media_type=_media_type(filename))

    @app.get("/api/waveform/{video_stem}/{filename}")
    def get_waveform(video_stem: str, filename: str, bars: int = 300):
        """Return downsampled RMS waveform data + highlight regions for a clip."""
        # Find clip file
        clip_path = output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        if not clip_path.exists():
            clip_path = output_dir / DIR_CLIPS / DIR_KEPT / video_stem / filename
        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")

        # Check for cached waveform sidecar
        cache_path = clip_path.with_suffix(clip_path.suffix + ".waveform.json")
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                # Attach highlight regions from metadata
                cached["highlight_regions"] = _get_highlight_regions(
                    output_dir, video_stem, filename
                )
                return cached
            except (json.JSONDecodeError, KeyError):
                pass

        # Extract audio via FFmpeg and compute RMS
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-i", str(clip_path), "-vn",
                    "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                    "-f", "s16le", "pipe:1",
                ],
                capture_output=True, timeout=30,
            )
            if result.returncode != 0:
                raise HTTPException(500, "FFmpeg audio extraction failed")

            samples = np.frombuffer(
                result.stdout, dtype=np.int16
            ).astype(np.float32) / 32768.0

            if len(samples) == 0:
                raise HTTPException(500, "No audio data extracted")

            num_bars = min(bars, len(samples))
            chunk_size = max(1, len(samples) // num_bars)
            waveform = []
            for i in range(0, len(samples), chunk_size):
                chunk = samples[i : i + chunk_size]
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                waveform.append(round(rms, 4))

            # Normalize to [0, 1]
            max_val = max(waveform) if waveform else 1.0
            if max_val > 0:
                waveform = [round(v / max_val, 4) for v in waveform]

            duration = len(samples) / 22050.0
            data = {
                "waveform": waveform,
                "duration": round(duration, 3),
                "sample_count": len(waveform),
            }

            # Cache to sidecar file
            try:
                cache_path.write_text(json.dumps(data))
            except OSError:
                pass

            data["highlight_regions"] = _get_highlight_regions(
                output_dir, video_stem, filename
            )
            return data

        except subprocess.TimeoutExpired:
            raise HTTPException(500, "Waveform extraction timed out")

    def _get_highlight_regions(out_dir: Path, video_stem: str, filename: str):
        """Load highlight_regions for a clip from its metadata file."""
        meta_path = out_dir / DIR_METADATA / f"{video_stem}_clips.json"
        if not meta_path.exists():
            return []
        try:
            clip_metas = load_metadata(meta_path)
            for clip in clip_metas:
                if clip.filename == filename:
                    return clip.highlight_regions or []
        except Exception:
            pass
        return []

    @app.post("/api/clips/{video_stem}/{filename}/keep")
    def keep_clip(video_stem: str, filename: str, req: KeepRequest = None):
        clip_path = output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        meta_path = output_dir / DIR_METADATA / f"{video_stem}_clips.json"

        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")

        kept_dir = output_dir / DIR_CLIPS / DIR_KEPT / video_stem
        kept_dir.mkdir(parents=True, exist_ok=True)
        dest = kept_dir / filename

        needs_trim = req and req.needs_trim

        if needs_trim:
            duration = req.trim_end - req.trim_start
            if duration < 1:
                raise HTTPException(400, "Trimmed clip would be too short")
            # Re-encode trimmed clip
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-ss", f"{req.trim_start:.3f}",
                    "-i", str(clip_path),
                    "-t", f"{duration:.3f}",
                    "-c:v", "libx264", "-c:a", "aac",
                    "-avoid_negative_ts", "make_zero",
                    str(dest),
                ],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise HTTPException(500, f"FFmpeg failed: {result.stderr[-200:]}")
        else:
            try:
                shutil.copy2(str(clip_path), str(dest))
            except OSError:
                pass

        update_clip_status(meta_path, filename, "kept")

        # Store custom name in metadata (no file rename to avoid locking)
        if req and req.custom_name and req.custom_name.strip():
            from clipcutter.metadata import update_clip_custom_name
            update_clip_custom_name(meta_path, filename, req.custom_name.strip())

        return {"status": "kept", "trimmed": bool(needs_trim)}

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

    # ------------------------------------------------------------------
    # Encoding API
    # ------------------------------------------------------------------

    @app.get("/api/encoding/presets")
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

    @app.get("/api/kept")
    def list_kept_clips():
        """List all kept clips with encoding and YouTube status from metadata."""
        kept_dir = output_dir / DIR_CLIPS / DIR_KEPT
        meta_dir = output_dir / DIR_METADATA

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
                    "custom_name": clip.custom_name,
                    "encoded_filename": clip.encoded_filename,
                    "encoding_preset": clip.encoding_preset,
                    "youtube_video_id": clip.youtube_video_id,
                    "youtube_url": clip.youtube_url,
                    "youtube_upload_status": clip.youtube_upload_status,
                }

                # Check if encoded file actually exists
                if clip.encoded_filename:
                    enc_path = output_dir / DIR_CLIPS / DIR_ENCODED / video_stem / clip.encoded_filename
                    clip_info["encoded_exists"] = enc_path.exists()
                    if enc_path.exists():
                        clip_info["encoded_video_url"] = f"/video/encoded/{video_stem}/{clip.encoded_filename}"
                else:
                    clip_info["encoded_exists"] = False

                clips.append(clip_info)

        clips.sort(key=lambda c: (-c["confidence"],))
        return {"clips": clips, "total": len(clips)}

    @app.post("/api/encode")
    def start_encoding(req: EncodeRequest):
        if enc_state.running:
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
            kept_path = output_dir / DIR_CLIPS / DIR_KEPT / clip_ref.video_stem / clip_ref.filename
            if not kept_path.exists():
                raise HTTPException(404, f"Clip not found: {clip_ref.video_stem}/{clip_ref.filename}")

        enc_state.reset(total=len(req.clips))

        def run():
            from clipcutter.encoder import encode_clip

            for i, clip_ref in enumerate(req.clips):
                if enc_state.cancelled:
                    break

                enc_state.set_current(clip_ref.filename, i + 1)
                input_path = output_dir / DIR_CLIPS / DIR_KEPT / clip_ref.video_stem / clip_ref.filename
                encoded_dir = output_dir / DIR_CLIPS / DIR_ENCODED / clip_ref.video_stem
                meta_path = output_dir / DIR_METADATA / f"{clip_ref.video_stem}_clips.json"

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
                    enc_state.add_completed(clip_ref.filename)
                except Exception as exc:
                    enc_state.add_error(clip_ref.filename, str(exc))

            enc_state.finish()

        threading.Thread(target=run, daemon=True).start()
        return {"status": "started"}

    @app.get("/api/encode/status")
    def encoding_status():
        return enc_state.snapshot()

    @app.post("/api/encode/cancel")
    def cancel_encoding():
        enc_state.cancelled = True
        return {"status": "cancelling"}

    @app.get("/video/encoded/{video_stem}/{filename}")
    def serve_encoded_video(video_stem: str, filename: str):
        clip_path = output_dir / DIR_CLIPS / DIR_ENCODED / video_stem / filename
        if not clip_path.exists():
            raise HTTPException(404, "Encoded clip not found")
        return FileResponse(clip_path, media_type=_media_type(filename))

    # ------------------------------------------------------------------
    # Compilation API
    # ------------------------------------------------------------------

    @app.post("/api/compilation")
    def start_compilation(req: CompilationRequest):
        if comp_state.running:
            raise HTTPException(409, "Compilation already in progress")
        if len(req.clips) < 2:
            raise HTTPException(400, "Need at least 2 clips for a compilation")

        # Resolve clip paths (prefer encoded, fall back to kept)
        clip_paths = []
        for ref in req.clips:
            # Try encoded first
            enc_dir = output_dir / DIR_CLIPS / DIR_ENCODED / ref.video_stem
            kept_path = output_dir / DIR_CLIPS / DIR_KEPT / ref.video_stem / ref.filename
            found = None

            if enc_dir.exists():
                meta_path = output_dir / DIR_METADATA / f"{ref.video_stem}_clips.json"
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

        comp_state.reset()

        comp_id = f"comp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        def run():
            from clipcutter.compiler import build_compilation
            from clipcutter.audio import get_video_duration

            try:
                comp_state.update("Getting clip durations...", 10)

                durations = [get_video_duration(p) for p in clip_paths]
                total_dur = sum(durations)
                if req.transition == "crossfade":
                    total_dur -= (len(durations) - 1) * req.crossfade_duration

                comp_state.update("Building compilation...", 30)

                comp_dir = output_dir / DIR_CLIPS / DIR_COMPILATIONS
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

                comp_state.update("Saving metadata...", 90)

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

                meta_dir = output_dir / DIR_METADATA
                meta_dir.mkdir(parents=True, exist_ok=True)
                meta_path = meta_dir / f"{comp_id}.json"
                meta_path.write_text(
                    json.dumps(meta.to_dict(), indent=2), encoding="utf-8"
                )

                comp_state.finish(filename=out_name)

            except Exception as exc:
                comp_state.finish(error=str(exc))

        threading.Thread(target=run, daemon=True).start()
        return {"status": "started", "compilation_id": comp_id}

    @app.get("/api/compilation/status")
    def compilation_status():
        return comp_state.snapshot()

    @app.post("/api/compilation/cancel")
    def cancel_compilation():
        comp_state.cancelled = True
        return {"status": "cancelling"}

    @app.get("/api/compilations")
    def list_compilations():
        """List all completed compilations."""
        meta_dir = output_dir / DIR_METADATA
        comp_dir = output_dir / DIR_CLIPS / DIR_COMPILATIONS
        comps = []

        if not meta_dir.exists():
            return {"compilations": []}

        for meta_path in sorted(meta_dir.glob("comp_*.json")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                # Check if file still exists
                if comp_dir.exists():
                    file_exists = (comp_dir / data.get("filename", "")).exists()
                else:
                    file_exists = False
                data["file_exists"] = file_exists
                comps.append(data)
            except (json.JSONDecodeError, KeyError):
                continue

        return {"compilations": comps}

    @app.delete("/api/compilation/{compilation_id}")
    def delete_compilation(compilation_id: str):
        meta_path = output_dir / DIR_METADATA / f"{compilation_id}.json"
        if not meta_path.exists():
            raise HTTPException(404, "Compilation not found")

        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            video_path = output_dir / DIR_CLIPS / DIR_COMPILATIONS / data.get("filename", "")
            if video_path.exists():
                video_path.unlink()
        except Exception:
            pass

        meta_path.unlink()
        return {"status": "deleted"}

    @app.get("/video/compilation/{filename}")
    def serve_compilation(filename: str):
        clip_path = output_dir / DIR_CLIPS / DIR_COMPILATIONS / filename
        if not clip_path.exists():
            raise HTTPException(404, "Compilation not found")
        return FileResponse(clip_path, media_type=_media_type(filename))

    # ------------------------------------------------------------------
    # YouTube API
    # ------------------------------------------------------------------

    @app.get("/api/youtube/status")
    def youtube_status():
        """Check if YouTube credentials exist and are valid."""
        from clipcutter.youtube import load_credentials, get_authenticated_service
        creds_path = output_dir / YOUTUBE_CREDENTIALS_FILE
        creds = load_credentials(creds_path)
        if creds is None:
            return {"authenticated": False}

        try:
            service, new_creds = get_authenticated_service(creds)
            # Quick test: list a single channel to verify credentials and get name
            resp = service.channels().list(part="snippet", mine=True).execute()
            channel_name = ""
            items = resp.get("items", [])
            if items:
                channel_name = items[0].get("snippet", {}).get("title", "")
            # Save refreshed credentials if they changed
            if new_creds.access_token != creds.access_token:
                from clipcutter.youtube import save_credentials
                save_credentials(new_creds, creds_path)
            return {"authenticated": True, "channel_name": channel_name}
        except Exception:
            return {"authenticated": False, "error": "Credentials expired or invalid"}

    @app.post("/api/youtube/auth/start")
    def youtube_auth_start(req: YouTubeAuthStartRequest, request: Request):
        """Save client_id/secret and return the OAuth2 authorization URL."""
        from clipcutter.youtube import get_auth_url, YouTubeCredentials, save_credentials

        # Determine redirect URI from request
        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/oauth/callback"

        auth_url = get_auth_url(req.client_id, redirect_uri)

        # Save partial credentials so we can use client_id/secret in callback
        partial_creds = YouTubeCredentials(
            access_token="",
            refresh_token="",
            token_expiry=None,
            client_id=req.client_id,
            client_secret=req.client_secret,
        )
        creds_path = output_dir / YOUTUBE_CREDENTIALS_FILE
        save_credentials(partial_creds, creds_path)

        return {"auth_url": auth_url}

    @app.get("/oauth/callback", response_class=HTMLResponse)
    def oauth_callback(code: str, request: Request):
        """Exchange authorization code for credentials."""
        from clipcutter.youtube import (
            exchange_code, load_credentials, save_credentials,
        )

        creds_path = output_dir / YOUTUBE_CREDENTIALS_FILE
        partial = load_credentials(creds_path)
        if partial is None:
            raise HTTPException(400, "No pending OAuth flow (client_id/secret missing)")

        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/oauth/callback"

        try:
            creds = exchange_code(
                code=code,
                client_id=partial.client_id,
                client_secret=partial.client_secret,
                redirect_uri=redirect_uri,
            )
            save_credentials(creds, creds_path)
        except Exception as exc:
            raise HTTPException(500, f"Token exchange failed: {exc}")

        return HTMLResponse("""
<!DOCTYPE html>
<html>
<head><title>ClipCutter - YouTube Auth</title></head>
<body style="background:#1a1a2e;color:#eee;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center">
<h2>YouTube Authentication Successful</h2>
<p>You can close this window.</p>
</div>
<script>
if (window.opener) {
    window.opener.postMessage({type: 'youtube-auth-success'}, window.location.origin);
    setTimeout(function() { window.close(); }, 1500);
}
</script>
</body>
</html>
""")

    @app.post("/api/youtube/auth/revoke")
    def youtube_auth_revoke():
        """Delete stored YouTube credentials."""
        creds_path = output_dir / YOUTUBE_CREDENTIALS_FILE
        if creds_path.exists():
            creds_path.unlink()
        return {"status": "revoked"}

    @app.get("/api/youtube/playlists")
    def youtube_playlists():
        from clipcutter.youtube import load_credentials, list_playlists
        creds_path = output_dir / YOUTUBE_CREDENTIALS_FILE
        creds = load_credentials(creds_path)
        if creds is None:
            raise HTTPException(401, "Not authenticated with YouTube")

        try:
            playlists = list_playlists(creds)
            return {"playlists": playlists}
        except Exception as exc:
            raise HTTPException(500, f"Failed to list playlists: {exc}")

    @app.post("/api/youtube/playlists")
    def youtube_create_playlist(req: PlaylistCreateRequest):
        from clipcutter.youtube import load_credentials, create_playlist
        creds_path = output_dir / YOUTUBE_CREDENTIALS_FILE
        creds = load_credentials(creds_path)
        if creds is None:
            raise HTTPException(401, "Not authenticated with YouTube")

        try:
            playlist = create_playlist(creds, req.title, req.privacy)
            return playlist
        except Exception as exc:
            raise HTTPException(500, f"Failed to create playlist: {exc}")

    @app.post("/api/youtube/upload")
    def start_youtube_upload(req: YouTubeBatchUploadRequest):
        if upl_state.running:
            raise HTTPException(409, "Upload already in progress")

        from clipcutter.youtube import load_credentials
        creds_path = output_dir / YOUTUBE_CREDENTIALS_FILE
        creds = load_credentials(creds_path)
        if creds is None:
            raise HTTPException(401, "Not authenticated with YouTube")

        upl_state.reset(total=len(req.clips))

        def run():
            from clipcutter.youtube import (
                upload_video, add_to_playlist, load_credentials, save_credentials,
            )

            current_creds = load_credentials(creds_path)

            for i, clip_req in enumerate(req.clips):
                if upl_state.cancelled:
                    break

                upl_state.set_current(clip_req.filename, i + 1)

                # Check for duplicate upload (skip if already uploaded)
                already_uploaded = False
                meta_path = output_dir / DIR_METADATA / f"{clip_req.video_stem}_clips.json"
                if meta_path.exists():
                    dup_metas = load_metadata(meta_path)
                    for dm in dup_metas:
                        if dm.filename == clip_req.filename and dm.youtube_video_id:
                            upl_state.add_completed(
                                clip_req.filename, dm.youtube_video_id, dm.youtube_url or "")
                            already_uploaded = True
                            break
                if already_uploaded:
                    continue

                # Determine which file to upload
                if clip_req.use_encoded:
                    # Look for encoded version via metadata
                    meta_path = output_dir / DIR_METADATA / f"{clip_req.video_stem}_clips.json"
                    file_path = None
                    if meta_path.exists():
                        clip_metas = load_metadata(meta_path)
                        for cm in clip_metas:
                            if cm.filename == clip_req.filename and cm.encoded_filename:
                                enc_path = (output_dir / DIR_CLIPS / DIR_ENCODED /
                                            clip_req.video_stem / cm.encoded_filename)
                                if enc_path.exists():
                                    file_path = enc_path
                                break

                    if file_path is None:
                        # Fall back to kept version
                        file_path = (output_dir / DIR_CLIPS / DIR_KEPT /
                                     clip_req.video_stem / clip_req.filename)
                else:
                    file_path = (output_dir / DIR_CLIPS / DIR_KEPT /
                                 clip_req.video_stem / clip_req.filename)

                if not file_path.exists():
                    upl_state.add_error(clip_req.filename, f"File not found: {file_path.name}")
                    continue

                def progress_cb(bytes_sent, bytes_total):
                    upl_state.update_progress(bytes_sent, bytes_total)

                result = upload_video(
                    creds=current_creds,
                    file_path=file_path,
                    title=clip_req.title,
                    description=clip_req.description,
                    tags=clip_req.tags,
                    category_id=clip_req.category_id,
                    privacy=clip_req.privacy,
                    progress_callback=progress_cb,
                )

                if result.success:
                    upl_state.add_completed(clip_req.filename, result.video_id, result.url)

                    # Update metadata
                    meta_path = output_dir / DIR_METADATA / f"{clip_req.video_stem}_clips.json"
                    if meta_path.exists():
                        update_clip_youtube(
                            meta_path, clip_req.filename,
                            result.video_id, result.url,
                        )

                    # Add to playlist if requested
                    if clip_req.playlist_id and result.video_id:
                        try:
                            add_to_playlist(current_creds, clip_req.playlist_id, result.video_id)
                        except Exception:
                            pass  # Non-fatal: upload succeeded
                else:
                    upl_state.add_error(clip_req.filename, result.error or "Unknown error")
                    # Record failure in metadata
                    meta_path = output_dir / DIR_METADATA / f"{clip_req.video_stem}_clips.json"
                    if meta_path.exists():
                        update_clip_youtube(
                            meta_path, clip_req.filename,
                            video_id="", url="", status="failed",
                        )

            upl_state.finish()

        threading.Thread(target=run, daemon=True).start()
        return {"status": "started"}

    @app.get("/api/youtube/upload/status")
    def youtube_upload_status():
        return upl_state.snapshot()

    @app.post("/api/youtube/upload/cancel")
    def cancel_youtube_upload():
        upl_state.cancelled = True
        return {"status": "cancelling"}

    return app
