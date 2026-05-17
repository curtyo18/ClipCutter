"""Review (clip keep/discard) and waveform endpoints."""
import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clipcutter.config import DIR_CLIPS, DIR_ENCODED, DIR_KEPT, DIR_METADATA, DIR_PENDING
from clipcutter.errors import FFmpegTimeoutError
from clipcutter.metadata import load_metadata, load_metadata_dict, update_clip_custom_name, update_clip_duration, update_clip_status
from clipcutter.routes._helpers import _media_type, _safe_join
from clipcutter.state import AppState

# Wall-clock ceiling on a single keep-path ffmpeg invocation. A keep is
# essentially a one-off trim/encode so the same 10-minute ceiling as the
# encode worker (clipcutter.encoder.FFMPEG_ENCODE_TIMEOUT) is the right
# scale — comfortable for healthy inputs but small enough that a stuck
# call surfaces instead of pinning the keep task forever.
KEEP_FFMPEG_TIMEOUT = 600

# Short ceiling for the ffprobe duration call. Without this a corrupt or
# unreadable clip can hang the keep request thread indefinitely.
KEEP_FFPROBE_TIMEOUT = 30


class Segment(BaseModel):
    start: float
    end: float


class KeepRequest(BaseModel):
    segments: list[Segment] = []
    custom_name: Optional[str] = None
    quality: str = "copy"  # "copy" | "precise" | "ultra"


def _probe_duration(clip_path: Path) -> float:
    """Probe clip duration with ffprobe; return 0.0 if probe fails.

    Raises FFmpegTimeoutError if ffprobe exceeds KEEP_FFPROBE_TIMEOUT —
    a corrupt clip used to hang the keep request thread indefinitely.
    """
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(clip_path)],
            capture_output=True, text=True,
            timeout=KEEP_FFPROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegTimeoutError(
            f"ffprobe timed out after {KEEP_FFPROBE_TIMEOUT}s probing {clip_path}"
        ) from exc
    try:
        data = json.loads(probe.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except Exception:
        return 0.0


def _normalise_segments(req: Optional[KeepRequest], clip_duration: float) -> list[Segment]:
    """Sort segments and substitute the full clip when none were provided."""
    segments = req.segments if req and req.segments else []
    if not segments:
        segments = [Segment(start=0.0, end=clip_duration or 9999.0)]
    return sorted(segments, key=lambda s: s.start)


class KeepCancelled(Exception):
    """Raised when a keep task is cancelled mid-subprocess. The worker
    catches this and reports the task as cancelled rather than errored."""


def _run_ffmpeg(cmd: list[str], *,
                cancel_event: Optional[threading.Event] = None,
                register_popen: Optional[Callable[[Optional[subprocess.Popen]], None]] = None,
                error_label: str = "FFmpeg failed") -> None:
    """Spawn an ffmpeg invocation as a cancellable Popen.

    - Stores the Popen via register_popen so an external cancel route can
      .terminate() it.
    - On non-zero exit, raises KeepCancelled if cancel_event was set
      (the most likely cause), or RuntimeError otherwise.
    - Clears the registered Popen in finally.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise KeepCancelled("cancelled before spawn")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if register_popen is not None:
        register_popen(proc)
    try:
        # Flat ceiling matches the encode worker. A hung keep ffmpeg
        # can't pin the request thread forever even if cancel isn't
        # pressed; the worker maps FFmpegTimeoutError to an "error"
        # status on the task so the UI shows it instead of spinning.
        try:
            _stdout, stderr = proc.communicate(timeout=KEEP_FFMPEG_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                _stdout, stderr = proc.communicate(timeout=5)
            except Exception:
                stderr = ""
            raise FFmpegTimeoutError(
                f"{error_label}: timed out after {KEEP_FFMPEG_TIMEOUT}s"
            )
    finally:
        if register_popen is not None:
            register_popen(None)

    if proc.returncode != 0:
        if cancel_event is not None and cancel_event.is_set():
            raise KeepCancelled("cancelled mid-ffmpeg")
        raise RuntimeError(f"{error_label}: {(stderr or '')[-200:]}")


def _do_keep(
    *,
    output_dir: Path,
    clip_path: Path,
    video_stem: str,
    filename: str,
    segments: list[Segment],
    clip_duration: float,
    quality: str,
    cancel_event: Optional[threading.Event] = None,
    register_popen: Optional[Callable[[Optional[subprocess.Popen]], None]] = None,
) -> bool:
    """Run the ffmpeg trim/copy for one keep task. Returns whether the file was trimmed.

    cancel_event + register_popen are optional so existing callers
    (e.g. tests) don't have to thread them through. When provided, every
    ffmpeg subprocess is launched via Popen and its handle is registered
    so the cancel route can terminate it; the event is also re-checked
    between subprocess launches.
    """
    kept_dir = output_dir / DIR_CLIPS / DIR_KEPT / video_stem
    kept_dir.mkdir(parents=True, exist_ok=True)
    dest = kept_dir / filename

    is_full_clip = (
        len(segments) == 1
        and segments[0].start <= 0.1
        and (clip_duration == 0 or (clip_duration - segments[0].end) <= 0.1)
    )

    if is_full_clip:
        # Pure file copy — no subprocess to cancel.
        try:
            shutil.copy2(str(clip_path), str(dest))
        except OSError:
            pass
        return False

    if len(segments) == 1:
        seg = segments[0]
        duration = seg.end - seg.start
        _run_ffmpeg(
            ["ffmpeg", "-y",
             "-ss", f"{seg.start:.3f}",
             "-i", str(clip_path),
             "-t", f"{duration:.3f}",
             "-c", "copy",
             "-avoid_negative_ts", "make_zero",
             str(dest)],
            cancel_event=cancel_event,
            register_popen=register_popen,
        )
        return True

    # Multiple segments
    if quality == "copy":
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            seg_files: list[Path] = []
            for j, seg in enumerate(segments):
                seg_path = tmp_dir / f"seg_{j}.mp4"
                _run_ffmpeg(
                    ["ffmpeg", "-y",
                     "-ss", f"{seg.start:.3f}",
                     "-t", f"{seg.end - seg.start:.3f}",
                     "-i", str(clip_path),
                     "-c", "copy",
                     "-avoid_negative_ts", "make_zero",
                     str(seg_path)],
                    cancel_event=cancel_event,
                    register_popen=register_popen,
                    error_label="FFmpeg segment extract failed",
                )
                seg_files.append(seg_path)

            filelist = tmp_dir / "filelist.txt"
            filelist.write_text(
                "\n".join(f"file '{p}'" for p in seg_files),
                encoding="utf-8",
            )

            _run_ffmpeg(
                ["ffmpeg", "-y",
                 "-f", "concat", "-safe", "0",
                 "-i", str(filelist),
                 "-c", "copy",
                 str(dest)],
                cancel_event=cancel_event,
                register_popen=register_popen,
                error_label="FFmpeg concat failed",
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return True

    # Re-encode (precise / ultra)
    crf = "16" if quality == "precise" else "0"
    inputs: list[str] = []
    for seg in segments:
        inputs += ["-ss", f"{seg.start:.3f}", "-t", f"{seg.end - seg.start:.3f}", "-i", str(clip_path)]
    n = len(segments)
    filter_inputs = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    filter_complex = f"{filter_inputs}concat=n={n}:v=1:a=1[v][a]"
    _run_ffmpeg(
        ["ffmpeg", "-y"]
        + inputs
        + ["-filter_complex", filter_complex,
           "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-crf", crf, "-c:a", "aac",
           str(dest)],
        cancel_event=cancel_event,
        register_popen=register_popen,
    )
    return True


def create_router(state: AppState) -> APIRouter:
    router = APIRouter()

    def _get_highlight_regions(video_stem: str, filename: str) -> list:
        # Inner helper is only called from already-validated routes, but we
        # re-validate cheaply so the helper is safe to reuse from elsewhere.
        try:
            meta_path = _safe_join(
                state.output_dir / DIR_METADATA, f"{video_stem}_clips.json"
            )
        except HTTPException:
            return []
        if not meta_path.exists():
            return []
        try:
            for clip in load_metadata(meta_path):
                if clip.filename == filename:
                    return clip.highlight_regions or []
        except Exception:
            pass
        return []

    @router.get("/api/clips")
    def list_clips():
        pending_dir = state.output_dir / DIR_CLIPS / DIR_PENDING
        meta_dir = state.output_dir / DIR_METADATA

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
                    "processed_at": meta_data.get("processed_at", ""),
                })

        clips.sort(key=lambda c: (c["processed_at"], c["confidence"]), reverse=True)
        return {"clips": clips, "total": len(clips)}

    @router.get("/video/{video_stem}/{filename}")
    def serve_video(video_stem: str, filename: str):
        pending_base = state.output_dir / DIR_CLIPS / DIR_PENDING
        kept_base = state.output_dir / DIR_CLIPS / DIR_KEPT
        encoded_base = state.output_dir / DIR_CLIPS / DIR_ENCODED
        clip_path = _safe_join(pending_base, video_stem, filename)
        if not clip_path.exists():
            clip_path = _safe_join(kept_base, video_stem, filename)
        if not clip_path.exists():
            clip_path = _safe_join(encoded_base, video_stem, filename)
        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")
        return FileResponse(clip_path, media_type=_media_type(filename))

    @router.get("/api/waveform/{video_stem}/{filename}")
    def get_waveform(video_stem: str, filename: str, bars: int = 300):
        """Return downsampled RMS waveform data + highlight regions for a clip."""
        pending_base = state.output_dir / DIR_CLIPS / DIR_PENDING
        kept_base = state.output_dir / DIR_CLIPS / DIR_KEPT
        clip_path = _safe_join(pending_base, video_stem, filename)
        if not clip_path.exists():
            clip_path = _safe_join(kept_base, video_stem, filename)
        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")

        # Check for cached waveform sidecar
        cache_path = clip_path.with_suffix(clip_path.suffix + ".waveform.json")
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                cached["highlight_regions"] = _get_highlight_regions(video_stem, filename)
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

            data["highlight_regions"] = _get_highlight_regions(video_stem, filename)
            return data

        except subprocess.TimeoutExpired:
            raise HTTPException(500, "Waveform extraction timed out")

    @router.post("/api/clips/{video_stem}/{filename}/keep")
    def keep_clip(video_stem: str, filename: str, req: KeepRequest = None):
        pending_base = state.output_dir / DIR_CLIPS / DIR_PENDING
        meta_base = state.output_dir / DIR_METADATA
        clip_path = _safe_join(pending_base, video_stem, filename)
        meta_path = _safe_join(meta_base, f"{video_stem}_clips.json")

        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")

        # Cheap synchronous probe so segment validation can return 4xx
        # immediately for obviously bad input, before we spawn a worker.
        clip_duration = _probe_duration(clip_path)
        segments = _normalise_segments(req, clip_duration)
        for seg in segments:
            if seg.end - seg.start < 1.0:
                raise HTTPException(400, f"Segment {seg.start:.1f}-{seg.end:.1f} is too short (min 1s)")

        custom_name = req.custom_name.strip() if (req and req.custom_name and req.custom_name.strip()) else None
        quality = req.quality if (req and req.quality in ("copy", "precise", "ultra")) else "copy"

        task_id = state.keep.start(video_stem, filename)
        cancel_event = state.keep.get_cancel_event(task_id)

        def register_popen(p: Optional[subprocess.Popen]) -> None:
            state.keep.set_popen(task_id, p)

        def worker() -> None:
            try:
                state.keep.update_step(
                    task_id,
                    "Trimming…" if len(segments) > 1 else
                    "Saving clip…" if (
                        len(segments) == 1
                        and segments[0].start <= 0.1
                        and (clip_duration == 0 or (clip_duration - segments[0].end) <= 0.1)
                    ) else "Trimming…",
                )
                trimmed = _do_keep(
                    output_dir=state.output_dir,
                    clip_path=clip_path,
                    video_stem=video_stem,
                    filename=filename,
                    segments=segments,
                    clip_duration=clip_duration,
                    quality=quality,
                    cancel_event=cancel_event,
                    register_popen=register_popen,
                )
                state.keep.update_step(task_id, "Updating metadata…")
                if not update_clip_status(meta_path, filename, "kept"):
                    raise RuntimeError(
                        f"Metadata missing entry for {filename} in {meta_path}"
                    )
                if trimmed:
                    new_duration = sum(seg.end - seg.start for seg in segments)
                    if not update_clip_duration(meta_path, filename, new_duration):
                        raise RuntimeError(
                            f"Metadata missing entry for {filename} in {meta_path}"
                        )
                if custom_name:
                    if not update_clip_custom_name(meta_path, filename, custom_name):
                        raise RuntimeError(
                            f"Metadata missing entry for {filename} in {meta_path}"
                        )
                state.keep.finish(task_id, trimmed=trimmed)
            except KeepCancelled:
                state.keep.finish(task_id, cancelled=True)
            except Exception as e:
                state.keep.finish(task_id, error=str(e))

        threading.Thread(target=worker, daemon=True).start()
        return {"task_id": task_id, "status": "started"}

    @router.get("/api/clips/keep/status")
    def keep_status():
        return state.keep.snapshot()

    @router.post("/api/clips/keep/{task_id}/cancel")
    def cancel_keep(task_id: str):
        """Cancel an in-flight keep task. Signals the event so the worker
        bails out between subprocess spawns, and terminates the active
        ffmpeg subprocess (if any) so a long single-stage trim doesn't
        have to finish naturally."""
        if not state.keep.cancel(task_id):
            raise HTTPException(404, "Keep task not found")

        # TOCTOU-safe: worker may clear popen between get and terminate.
        proc = state.keep.get_popen(task_id)
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

    @router.post("/api/clips/{video_stem}/{filename}/discard")
    def discard_clip(video_stem: str, filename: str):
        pending_base = state.output_dir / DIR_CLIPS / DIR_PENDING
        meta_base = state.output_dir / DIR_METADATA
        clip_path = _safe_join(pending_base, video_stem, filename)
        meta_path = _safe_join(meta_base, f"{video_stem}_clips.json")

        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")

        if not update_clip_status(meta_path, filename, "discarded"):
            raise HTTPException(404, "Clip not found in metadata")
        return {"status": "discarded"}

    return router
