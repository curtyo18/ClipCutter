"""Review (clip keep/discard) and waveform endpoints."""
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clipcutter.config import DIR_CLIPS, DIR_ENCODED, DIR_KEPT, DIR_METADATA, DIR_PENDING
from clipcutter.metadata import load_metadata, load_metadata_dict, update_clip_custom_name, update_clip_duration, update_clip_status
from clipcutter.routes._helpers import _media_type
from clipcutter.state import AppState


class Segment(BaseModel):
    start: float
    end: float


class KeepRequest(BaseModel):
    segments: list[Segment] = []
    custom_name: Optional[str] = None


def create_router(state: AppState) -> APIRouter:
    router = APIRouter()

    def _get_highlight_regions(video_stem: str, filename: str) -> list:
        meta_path = state.output_dir / DIR_METADATA / f"{video_stem}_clips.json"
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
        clip_path = state.output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        if not clip_path.exists():
            clip_path = state.output_dir / DIR_CLIPS / DIR_KEPT / video_stem / filename
        if not clip_path.exists():
            clip_path = state.output_dir / DIR_CLIPS / DIR_ENCODED / video_stem / filename
        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")
        return FileResponse(clip_path, media_type=_media_type(filename))

    @router.get("/api/waveform/{video_stem}/{filename}")
    def get_waveform(video_stem: str, filename: str, bars: int = 300):
        """Return downsampled RMS waveform data + highlight regions for a clip."""
        clip_path = state.output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        if not clip_path.exists():
            clip_path = state.output_dir / DIR_CLIPS / DIR_KEPT / video_stem / filename
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
        clip_path = state.output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        meta_path = state.output_dir / DIR_METADATA / f"{video_stem}_clips.json"

        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")

        # Normalise segments: use full clip if none provided
        segments = req.segments if req and req.segments else []
        # Determine clip duration from the file (needed to detect full-clip case)
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(clip_path)],
            capture_output=True, text=True,
        )
        clip_duration = 0.0
        try:
            probe_data = json.loads(probe.stdout)
            clip_duration = float(probe_data.get("format", {}).get("duration", 0))
        except Exception:
            pass

        if not segments:
            segments = [Segment(start=0.0, end=clip_duration or 9999.0)]

        # Sort and validate
        segments = sorted(segments, key=lambda s: s.start)
        for seg in segments:
            if seg.end - seg.start < 1.0:
                raise HTTPException(400, f"Segment {seg.start:.1f}-{seg.end:.1f} is too short (min 1s)")

        kept_dir = state.output_dir / DIR_CLIPS / DIR_KEPT / video_stem
        kept_dir.mkdir(parents=True, exist_ok=True)
        dest = kept_dir / filename

        is_full_clip = (
            len(segments) == 1
            and segments[0].start <= 0.1
            and (clip_duration == 0 or (clip_duration - segments[0].end) <= 0.1)
        )

        if is_full_clip:
            # No re-encode needed
            try:
                shutil.copy2(str(clip_path), str(dest))
            except OSError:
                pass
            trimmed = False
        elif len(segments) == 1:
            # Single segment trim
            seg = segments[0]
            duration = seg.end - seg.start
            result = subprocess.run(
                ["ffmpeg", "-y",
                 "-ss", f"{seg.start:.3f}",
                 "-i", str(clip_path),
                 "-t", f"{duration:.3f}",
                 "-c:v", "libx264", "-c:a", "aac",
                 "-avoid_negative_ts", "make_zero",
                 str(dest)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise HTTPException(500, f"FFmpeg failed: {result.stderr[-200:]}")
            trimmed = True
        else:
            # Multiple segments: use concat filter
            inputs = []
            for seg in segments:
                inputs += ["-ss", f"{seg.start:.3f}", "-t", f"{seg.end - seg.start:.3f}", "-i", str(clip_path)]

            n = len(segments)
            filter_inputs = "".join(f"[{i}:v][{i}:a]" for i in range(n))
            filter_complex = f"{filter_inputs}concat=n={n}:v=1:a=1[v][a]"

            result = subprocess.run(
                ["ffmpeg", "-y"]
                + inputs
                + ["-filter_complex", filter_complex,
                   "-map", "[v]", "-map", "[a]",
                   "-c:v", "libx264", "-c:a", "aac",
                   str(dest)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise HTTPException(500, f"FFmpeg failed: {result.stderr[-200:]}")
            trimmed = True

        update_clip_status(meta_path, filename, "kept")

        if trimmed:
            new_duration = sum(seg.end - seg.start for seg in segments)
            update_clip_duration(meta_path, filename, new_duration)

        if req and req.custom_name and req.custom_name.strip():
            update_clip_custom_name(meta_path, filename, req.custom_name.strip())

        return {"status": "kept", "trimmed": trimmed}

    @router.post("/api/clips/{video_stem}/{filename}/discard")
    def discard_clip(video_stem: str, filename: str):
        clip_path = state.output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        meta_path = state.output_dir / DIR_METADATA / f"{video_stem}_clips.json"

        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")

        update_clip_status(meta_path, filename, "discarded")
        return {"status": "discarded"}

    return router
