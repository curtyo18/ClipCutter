# ClipCutter Todo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 3 bugs, refactor into modular FastAPI routers + Vite/TypeScript frontend, then add delete-after-compilation and multi-segment clip cutting features.

**Architecture:** Three sequential phases. Phase 1 fixes bugs in the existing files. Phase 2 splits `web.py` into 5 APIRouters and migrates `index.html` (2150 lines of inline JS) to typed TypeScript modules built by Vite. Phase 3 adds two new features on the clean codebase. Each phase ends with a full test run before the next begins.

**Tech Stack:** Python 3.10+, FastAPI + APIRouter, Pydantic, FFmpeg subprocess, Vite 5+, TypeScript (no framework), existing librosa/scipy pipeline.

---

## File Map

**Created:**
- `clipcutter/state.py` — ProcessingState, EncodingState, UploadState, CompilationState, LogWriter, AppState
- `clipcutter/routes/__init__.py` — empty package marker
- `clipcutter/routes/_helpers.py` — `_sanitize_filename()`, `_media_type()`
- `clipcutter/routes/process.py` — `/api/defaults`, `/api/process`, `/api/process/status`, `/api/sources`, `/api/sources/{stem}/delete`
- `clipcutter/routes/review.py` — `/api/clips`, `/api/waveform/...`, `/video/...`, `/api/clips/.../keep`, `/api/clips/.../discard`
- `clipcutter/routes/encode.py` — `/api/encoding/presets`, `/api/kept`, `/api/encode`, `/api/encode/status`, `/api/encode/cancel`, `/video/encoded/...`
- `clipcutter/routes/compile.py` — `/api/compilation`, `/api/compilation/status`, `/api/compilation/cancel`, `/api/compilations`, `/api/compilation/{id}`, `/video/compilation/...`, `/api/compilation/{id}/sources` (Phase 3)
- `clipcutter/routes/youtube.py` — all `/api/youtube/...` and `/oauth/...` endpoints
- `clipcutter/static/package.json`, `vite.config.ts`, `tsconfig.json`
- `clipcutter/static/src/api.ts`, `utils.ts`, `waveform.ts`
- `clipcutter/static/src/tabs/process.ts`, `review.ts`, `encode.ts`, `compile.ts`
- `clipcutter/static/src/main.ts`

**Modified:**
- `clipcutter/web.py` — Phase 1: KeepRequest fix; Phase 2: thin factory; frontend step: serve `dist/`
- `clipcutter/static/index.html` — Phase 1: clip action fix; Phase 2: replaced by Vite entry
- `.gitignore` — add `clipcutter/static/dist/` and `clipcutter/static/node_modules/`

---

## Phase 1: Quick Fixes

### Task 1: Fix trim always re-encoding

**Files:**
- Modify: `clipcutter/web.py:35-38` (KeepRequest model)
- Modify: `clipcutter/web.py:599` (needs_trim check)
- Modify: `clipcutter/static/index.html:1127` (fetch body)
- Test: `tests/`

- [ ] **Step 1: Add `needs_trim` to KeepRequest in web.py**

Change lines 35–38:
```python
class KeepRequest(BaseModel):
    trim_start: float = 0.0
    trim_end: float = 0.0
    needs_trim: bool = False
    custom_name: Optional[str] = None
```

- [ ] **Step 2: Fix the server-side condition in web.py**

Change line 599 from:
```python
        needs_trim = req and (req.trim_start > 0.1 or req.trim_end > 0.1)
```
To:
```python
        needs_trim = req and req.needs_trim
```

- [ ] **Step 3: Send `needs_trim` from the frontend**

Change `index.html:1127` from:
```javascript
    const body = {trim_start: trimStart, trim_end: trimEnd, custom_name: customName};
```
To:
```javascript
    const body = {trim_start: trimStart, trim_end: trimEnd, needs_trim: needsTrim, custom_name: customName};
```

- [ ] **Step 4: Run tests**

```bash
cd E:/Projects/ClipCutter && pytest tests/ -v -k "not browser"
```
Expected: all API tests pass.

- [ ] **Step 5: Commit**

```bash
git add clipcutter/web.py clipcutter/static/index.html
git commit -m "fix: trim re-encoding only when trim points actually changed"
```

---

### Task 2: Pause video when keeping or discarding

**Files:**
- Modify: `clipcutter/static/index.html:1116` (clipAction function)

- [ ] **Step 1: Pause the video at the start of clipAction**

Change `index.html:1116`:
```javascript
async function clipAction(type) {
  const clip = clips[currentIndex];
  const player = document.getElementById('player');
  if (player) player.pause();
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/ -v -k "not browser"
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add clipcutter/static/index.html
git commit -m "fix: pause video when saving or discarding a clip"
```

---

### Task 3: Show custom name in encode section

**Files:**
- Modify: `clipcutter/static/index.html:1343`

- [ ] **Step 1: Show custom_name if set**

Change `index.html:1343` from:
```javascript
      html += `<span class="clip-name" title="${escapeHtml(clip.filename)}">${escapeHtml(clip.filename)}</span>`;
```
To:
```javascript
      html += `<span class="clip-name" title="${escapeHtml(clip.filename)}">${escapeHtml(clip.custom_name || clip.filename)}</span>`;
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/ -v -k "not browser"
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add clipcutter/static/index.html
git commit -m "fix: show custom name in encode section when set"
```

---

## Phase 2: Architecture Refactor — Backend

### Task 4: Create clipcutter/state.py

**Files:**
- Create: `clipcutter/state.py`

- [ ] **Step 1: Create state.py with all state classes moved from web.py**

```python
"""Shared application state for ClipCutter web server."""
import sys
import threading
from pathlib import Path
from typing import Optional


class ProcessingState:
    """Thread-safe processing state."""

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
            return {"running": self.running, "log": list(self.log_lines), "error": self.error}


class LogWriter:
    """Captures stdout writes into ProcessingState."""

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


class EncodingState:
    """Thread-safe encoding state."""

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
    """Thread-safe upload state."""

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
            self.completed.append({"filename": filename, "video_id": video_id, "url": url})

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


class AppState:
    """Container for all shared state, instantiated once per app."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.proc = ProcessingState()
        self.enc = EncodingState()
        self.upl = UploadState()
        self.comp = CompilationState()
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/state.py
git commit -m "refactor: extract state classes to state.py"
```

---

### Task 5: Create routes package and helpers

**Files:**
- Create: `clipcutter/routes/__init__.py`
- Create: `clipcutter/routes/_helpers.py`

- [ ] **Step 1: Create empty package marker**

```python
# clipcutter/routes/__init__.py
```

- [ ] **Step 2: Create _helpers.py**

```python
"""Shared helper utilities for route handlers."""
from pathlib import Path


def _sanitize_filename(name: str) -> str:
    """Strip unsafe chars, replace spaces with underscores."""
    safe = "".join(c for c in name if c.isalnum() or c in " _-").strip()
    return safe.replace(" ", "_")


def _media_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {"webm": "video/webm", ".gif": "image/gif"}.get(ext, "video/mp4")
```

- [ ] **Step 3: Commit**

```bash
git add clipcutter/routes/__init__.py clipcutter/routes/_helpers.py
git commit -m "refactor: add routes package and shared helpers"
```

---

### Task 6: Create routes/process.py

**Files:**
- Create: `clipcutter/routes/process.py`

- [ ] **Step 1: Create process router**

```python
"""Process and source video management endpoints."""
import sys
import threading
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
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/routes/process.py
git commit -m "refactor: extract process endpoints to routes/process.py"
```

---

### Task 7: Create routes/review.py

**Files:**
- Create: `clipcutter/routes/review.py`

- [ ] **Step 1: Create review router**

```python
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
from clipcutter.metadata import (
    load_metadata, load_metadata_dict, update_clip_custom_name, update_clip_status,
)
from clipcutter.routes._helpers import _media_type
from clipcutter.state import AppState


class KeepRequest(BaseModel):
    trim_start: float = 0.0
    trim_end: float = 0.0
    needs_trim: bool = False
    custom_name: Optional[str] = None


def _get_highlight_regions(output_dir: Path, video_stem: str, filename: str) -> list:
    meta_path = output_dir / DIR_METADATA / f"{video_stem}_clips.json"
    if not meta_path.exists():
        return []
    try:
        for clip in load_metadata(meta_path):
            if clip.filename == filename:
                return clip.highlight_regions or []
    except Exception:
        pass
    return []


def create_router(state: AppState) -> APIRouter:
    router = APIRouter()

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
            for clip in load_metadata(meta_path):
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

    @router.get("/video/{video_stem}/{filename}")
    def serve_video(video_stem: str, filename: str):
        for subdir in (DIR_PENDING, DIR_KEPT, DIR_ENCODED):
            p = state.output_dir / DIR_CLIPS / subdir / video_stem / filename
            if p.exists():
                return FileResponse(p, media_type=_media_type(filename))
        raise HTTPException(404, "Clip not found")

    @router.get("/api/waveform/{video_stem}/{filename}")
    def get_waveform(video_stem: str, filename: str, bars: int = 300):
        clip_path = state.output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        if not clip_path.exists():
            clip_path = state.output_dir / DIR_CLIPS / DIR_KEPT / video_stem / filename
        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")

        cache_path = clip_path.with_suffix(clip_path.suffix + ".waveform.json")
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                cached["highlight_regions"] = _get_highlight_regions(
                    state.output_dir, video_stem, filename
                )
                return cached
            except (json.JSONDecodeError, KeyError):
                pass

        try:
            result = subprocess.run(
                ["ffmpeg", "-i", str(clip_path), "-vn", "-acodec", "pcm_s16le",
                 "-ar", "22050", "-ac", "1", "-f", "s16le", "pipe:1"],
                capture_output=True, timeout=30,
            )
            if result.returncode != 0:
                raise HTTPException(500, "FFmpeg audio extraction failed")

            samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
            if len(samples) == 0:
                raise HTTPException(500, "No audio data extracted")

            num_bars = min(bars, len(samples))
            chunk_size = max(1, len(samples) // num_bars)
            waveform = []
            for i in range(0, len(samples), chunk_size):
                chunk = samples[i: i + chunk_size]
                waveform.append(round(float(np.sqrt(np.mean(chunk ** 2))), 4))

            max_val = max(waveform) if waveform else 1.0
            if max_val > 0:
                waveform = [round(v / max_val, 4) for v in waveform]

            duration = len(samples) / 22050.0
            data = {"waveform": waveform, "duration": round(duration, 3), "sample_count": len(waveform)}
            try:
                cache_path.write_text(json.dumps(data))
            except OSError:
                pass

            data["highlight_regions"] = _get_highlight_regions(state.output_dir, video_stem, filename)
            return data

        except subprocess.TimeoutExpired:
            raise HTTPException(500, "Waveform extraction timed out")

    @router.post("/api/clips/{video_stem}/{filename}/keep")
    def keep_clip(video_stem: str, filename: str, req: KeepRequest = None):
        clip_path = state.output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        meta_path = state.output_dir / DIR_METADATA / f"{video_stem}_clips.json"

        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")

        kept_dir = state.output_dir / DIR_CLIPS / DIR_KEPT / video_stem
        kept_dir.mkdir(parents=True, exist_ok=True)
        dest = kept_dir / filename

        needs_trim = req and req.needs_trim

        if needs_trim:
            duration = req.trim_end - req.trim_start
            if duration < 1:
                raise HTTPException(400, "Trimmed clip would be too short")
            result = subprocess.run(
                ["ffmpeg", "-y",
                 "-ss", f"{req.trim_start:.3f}",
                 "-i", str(clip_path),
                 "-t", f"{duration:.3f}",
                 "-c:v", "libx264", "-c:a", "aac",
                 "-avoid_negative_ts", "make_zero",
                 str(dest)],
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

        if req and req.custom_name and req.custom_name.strip():
            update_clip_custom_name(meta_path, filename, req.custom_name.strip())

        return {"status": "kept", "trimmed": bool(needs_trim)}

    @router.post("/api/clips/{video_stem}/{filename}/discard")
    def discard_clip(video_stem: str, filename: str):
        clip_path = state.output_dir / DIR_CLIPS / DIR_PENDING / video_stem / filename
        meta_path = state.output_dir / DIR_METADATA / f"{video_stem}_clips.json"

        if not clip_path.exists():
            raise HTTPException(404, "Clip not found")

        update_clip_status(meta_path, filename, "discarded")
        return {"status": "discarded"}

    return router
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/routes/review.py
git commit -m "refactor: extract review endpoints to routes/review.py"
```

---

### Task 8: Create routes/encode.py

**Files:**
- Create: `clipcutter/routes/encode.py`

- [ ] **Step 1: Create encode router**

```python
"""Encoding endpoints and kept clip listing."""
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clipcutter.config import (
    DEFAULT_ENCODING_PRESET, DIR_CLIPS, DIR_ENCODED, DIR_KEPT, DIR_METADATA,
)
from clipcutter.metadata import load_metadata, update_clip_encoding
from clipcutter.routes._helpers import _media_type, _sanitize_filename
from clipcutter.state import AppState


class EncodeClipRef(BaseModel):
    video_stem: str
    filename: str


class EncodeRequest(BaseModel):
    clips: List[EncodeClipRef]
    preset: str
    target_fps: Optional[int] = None
    slowdown_factor: Optional[float] = None


def create_router(state: AppState) -> APIRouter:
    router = APIRouter()

    @router.get("/api/encoding/presets")
    def list_encoding_presets():
        from clipcutter.encoder import get_presets
        presets = get_presets()
        result = [
            {"name": name, "display_name": p.display_name, "extension": p.extension or "(same as source)"}
            for name, p in presets.items()
        ]
        return {"presets": result, "default": DEFAULT_ENCODING_PRESET, "fps_options": [None, 24, 30, 60]}

    @router.get("/api/kept")
    def list_kept_clips():
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

            from clipcutter.metadata import load_metadata_dict
            meta_data = load_metadata_dict(meta_path)
            source_video = meta_data.get("source_video", video_stem)

            for clip in load_metadata(meta_path):
                if clip.status != "kept":
                    continue
                if not (video_dir / clip.filename).exists():
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
                    "encoded_exists": False,
                }

                if clip.encoded_filename:
                    enc_path = state.output_dir / DIR_CLIPS / DIR_ENCODED / video_stem / clip.encoded_filename
                    clip_info["encoded_exists"] = enc_path.exists()
                    if enc_path.exists():
                        clip_info["encoded_video_url"] = f"/video/encoded/{video_stem}/{clip.encoded_filename}"

                clips.append(clip_info)

        clips.sort(key=lambda c: (-c["confidence"],))
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

                custom_stem = None
                if meta_path.exists():
                    for cm in load_metadata(meta_path):
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

    return router
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/routes/encode.py
git commit -m "refactor: extract encode endpoints to routes/encode.py"
```

---

### Task 9: Create routes/compile.py

**Files:**
- Create: `clipcutter/routes/compile.py`

- [ ] **Step 1: Create compile router**

```python
"""Compilation build and management endpoints."""
import json
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clipcutter.config import (
    DIR_CLIPS, DIR_COMPILATIONS, DIR_ENCODED, DIR_KEPT, DIR_METADATA,
)
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

        clip_paths = []
        custom_names = []
        for ref in req.clips:
            # Prefer encoded, fall back to kept
            clip_path = None
            enc_dir = state.output_dir / DIR_CLIPS / DIR_ENCODED / ref.video_stem
            meta_path = state.output_dir / DIR_METADATA / f"{ref.video_stem}_clips.json"
            if meta_path.exists():
                for cm in load_metadata(meta_path):
                    if cm.filename == ref.filename and cm.encoded_filename:
                        enc_path = enc_dir / cm.encoded_filename
                        if enc_path.exists():
                            clip_path = enc_path
                    break

            if clip_path is None:
                clip_path = state.output_dir / DIR_CLIPS / DIR_KEPT / ref.video_stem / ref.filename

            if not clip_path.exists():
                raise HTTPException(404, f"Clip not found: {ref.video_stem}/{ref.filename}")

            clip_paths.append(clip_path)

            # Collect custom name for metadata
            custom_name = ref.filename
            if meta_path.exists():
                for cm in load_metadata(meta_path):
                    if cm.filename == ref.filename:
                        custom_name = cm.custom_name or ref.filename
                        break
            custom_names.append(custom_name)

        from datetime import datetime
        comp_id = f"comp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        state.comp.reset()

        def run():
            try:
                state.comp.update("Building compilation...", 30)
                from clipcutter.compiler import build_compilation
                from clipcutter.encoder import get_presets
                from clipcutter.config import DEFAULT_ENCODING_PRESET

                presets = get_presets()
                preset_name = req.preset if req.preset in presets else DEFAULT_ENCODING_PRESET
                preset_obj = presets[preset_name]

                comp_dir = state.output_dir / DIR_CLIPS / DIR_COMPILATIONS
                comp_dir.mkdir(parents=True, exist_ok=True)

                title_slug = _sanitize_filename(req.title or comp_id)
                ext = preset_obj.extension or ".mp4"
                out_filename = f"{comp_id}_{title_slug}{ext}"
                out_path = comp_dir / out_filename

                build_compilation(
                    clip_paths=clip_paths,
                    output_path=out_path,
                    transition=req.transition,
                    crossfade_duration=req.crossfade_duration,
                    preset=preset_obj,
                    progress_callback=lambda step, pct: state.comp.update(step, pct),
                    cancelled_flag=lambda: state.comp.cancelled,
                )

                if state.comp.cancelled:
                    if out_path.exists():
                        out_path.unlink()
                    state.comp.finish(error="Cancelled")
                    return

                # Save compilation metadata
                meta = {
                    "compilation_id": comp_id,
                    "filename": out_filename,
                    "title": req.title,
                    "transition": req.transition,
                    "clips": [
                        {"video_stem": ref.video_stem, "filename": ref.filename, "custom_name": custom_names[i]}
                        for i, ref in enumerate(req.clips)
                    ],
                    "clip_count": len(req.clips),
                    "total_duration": sum(
                        (p.stat().st_size for p in clip_paths if p.exists()), 0
                    ),
                    "created_at": datetime.now().isoformat(),
                }
                meta_path = state.output_dir / DIR_METADATA / f"{comp_id}.json"
                meta_path.write_text(json.dumps(meta, indent=2))

                state.comp.finish(filename=out_filename)
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
        meta_dir = state.output_dir / DIR_METADATA
        comp_dir = state.output_dir / DIR_CLIPS / DIR_COMPILATIONS
        comps = []

        if not meta_dir.exists():
            return {"compilations": []}

        for meta_path in sorted(meta_dir.glob("comp_*.json")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                data["file_exists"] = comp_dir.exists() and (comp_dir / data.get("filename", "")).exists()
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

    @router.get("/video/compilation/{filename}")
    def serve_compilation(filename: str):
        clip_path = state.output_dir / DIR_CLIPS / DIR_COMPILATIONS / filename
        if not clip_path.exists():
            raise HTTPException(404, "Compilation not found")
        return FileResponse(clip_path, media_type=_media_type(filename))

    return router
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/routes/compile.py
git commit -m "refactor: extract compilation endpoints to routes/compile.py"
```

---

### Task 10: Create routes/youtube.py

**Files:**
- Create: `clipcutter/routes/youtube.py`

- [ ] **Step 1: Create youtube router (move all youtube/oauth endpoints from web.py)**

Copy the 12 YouTube endpoints from `clipcutter/web.py` lines 1054–1309 into a router, replacing `app.` with `router.` and `output_dir` with `state.output_dir`, `upl_state` with `state.upl`. The `creds_path` becomes `state.output_dir / YOUTUBE_CREDENTIALS_FILE`.

```python
"""YouTube authentication and upload endpoints."""
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from clipcutter.config import (
    DIR_CLIPS, DIR_ENCODED, DIR_KEPT, DIR_METADATA,
    YOUTUBE_CREDENTIALS_FILE, YOUTUBE_DEFAULT_CATEGORY, YOUTUBE_DEFAULT_PRIVACY,
)
from clipcutter.metadata import load_metadata, update_clip_youtube
from clipcutter.state import AppState


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


def create_router(state: AppState) -> APIRouter:
    router = APIRouter()
    creds_path = state.output_dir / YOUTUBE_CREDENTIALS_FILE

    @router.get("/api/youtube/status")
    def youtube_status():
        from clipcutter.youtube import load_credentials, get_authenticated_service
        creds = load_credentials(creds_path)
        if creds is None:
            return {"authenticated": False}
        try:
            service, new_creds = get_authenticated_service(creds)
            resp = service.channels().list(part="snippet", mine=True).execute()
            channel_name = ""
            items = resp.get("items", [])
            if items:
                channel_name = items[0].get("snippet", {}).get("title", "")
            if new_creds.access_token != creds.access_token:
                from clipcutter.youtube import save_credentials
                save_credentials(new_creds, creds_path)
            return {"authenticated": True, "channel_name": channel_name}
        except Exception:
            return {"authenticated": False, "error": "Credentials expired or invalid"}

    @router.post("/api/youtube/auth/start")
    def youtube_auth_start(req: YouTubeAuthStartRequest, request: Request):
        from clipcutter.youtube import get_auth_url, YouTubeCredentials, save_credentials
        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/oauth/callback"
        auth_url = get_auth_url(req.client_id, redirect_uri)
        partial_creds = YouTubeCredentials(
            access_token="", refresh_token="", token_expiry=None,
            client_id=req.client_id, client_secret=req.client_secret,
            redirect_uri=redirect_uri,
        )
        save_credentials(partial_creds, creds_path)
        return {"auth_url": auth_url}

    @router.get("/oauth/callback")
    def oauth_callback(code: str = None, error: str = None):
        from fastapi.responses import HTMLResponse
        if error or not code:
            return HTMLResponse("<script>window.opener.postMessage({type:'youtube-auth-error'},window.location.origin);window.close();</script>")
        from clipcutter.youtube import exchange_code, load_credentials, save_credentials
        partial = load_credentials(creds_path)
        if partial is None:
            return HTMLResponse("<script>window.close();</script>")
        try:
            creds = exchange_code(code, partial.client_id, partial.client_secret, partial.redirect_uri)
            save_credentials(creds, creds_path)
        except Exception:
            pass
        return HTMLResponse("<script>window.opener.postMessage({type:'youtube-auth-success'},window.location.origin);window.close();</script>")

    @router.post("/api/youtube/auth/revoke")
    def youtube_auth_revoke():
        if creds_path.exists():
            creds_path.unlink()
        return {"status": "revoked"}

    @router.get("/api/youtube/playlists")
    def list_playlists():
        from clipcutter.youtube import load_credentials, get_authenticated_service, list_playlists as yt_list
        creds = load_credentials(creds_path)
        if creds is None:
            raise HTTPException(401, "Not authenticated")
        service, _ = get_authenticated_service(creds)
        playlists = yt_list(service)
        return {"playlists": [{"id": p.id, "title": p.title, "item_count": p.item_count} for p in playlists]}

    @router.post("/api/youtube/playlists")
    def create_playlist(req: PlaylistCreateRequest):
        from clipcutter.youtube import load_credentials, get_authenticated_service, create_playlist as yt_create
        creds = load_credentials(creds_path)
        if creds is None:
            raise HTTPException(401, "Not authenticated")
        service, _ = get_authenticated_service(creds)
        pl = yt_create(service, req.title, req.privacy)
        return {"id": pl.id, "title": pl.title, "item_count": 0}

    @router.post("/api/youtube/upload")
    def start_upload(req: YouTubeBatchUploadRequest):
        if state.upl.running:
            raise HTTPException(409, "Upload already in progress")
        if not req.clips:
            raise HTTPException(400, "No clips to upload")

        from clipcutter.youtube import load_credentials
        current_creds = load_credentials(creds_path)
        if current_creds is None:
            raise HTTPException(401, "Not authenticated")

        state.upl.reset(total=len(req.clips))

        def run():
            from clipcutter.youtube import upload_video, add_to_playlist, load_credentials, save_credentials
            curr = load_credentials(creds_path)

            for i, clip_req in enumerate(req.clips):
                if state.upl.cancelled:
                    break
                state.upl.set_current(clip_req.filename, i + 1)

                # Skip if already uploaded
                meta_path = state.output_dir / DIR_METADATA / f"{clip_req.video_stem}_clips.json"
                already = False
                if meta_path.exists():
                    for dm in load_metadata(meta_path):
                        if dm.filename == clip_req.filename and dm.youtube_video_id:
                            state.upl.add_completed(clip_req.filename, dm.youtube_video_id, dm.youtube_url or "")
                            already = True
                            break
                if already:
                    continue

                # Resolve file path
                file_path = None
                if clip_req.use_encoded and meta_path.exists():
                    for cm in load_metadata(meta_path):
                        if cm.filename == clip_req.filename and cm.encoded_filename:
                            ep = state.output_dir / DIR_CLIPS / DIR_ENCODED / clip_req.video_stem / cm.encoded_filename
                            if ep.exists():
                                file_path = ep
                            break
                if file_path is None:
                    file_path = state.output_dir / DIR_CLIPS / DIR_KEPT / clip_req.video_stem / clip_req.filename
                if not file_path.exists():
                    state.upl.add_error(clip_req.filename, f"File not found: {file_path.name}")
                    continue

                result = upload_video(
                    creds=curr, file_path=file_path,
                    title=clip_req.title, description=clip_req.description,
                    tags=clip_req.tags, category_id=clip_req.category_id,
                    privacy=clip_req.privacy,
                    progress_callback=lambda s, t: state.upl.update_progress(s, t),
                )

                if result.success:
                    state.upl.add_completed(clip_req.filename, result.video_id, result.url)
                    if meta_path.exists():
                        update_clip_youtube(meta_path, clip_req.filename, result.video_id, result.url)
                    if clip_req.playlist_id and result.video_id:
                        try:
                            add_to_playlist(curr, clip_req.playlist_id, result.video_id)
                        except Exception:
                            pass
                else:
                    state.upl.add_error(clip_req.filename, result.error or "Unknown error")
                    if meta_path.exists():
                        update_clip_youtube(meta_path, clip_req.filename, video_id="", url="", status="failed")

            state.upl.finish()

        threading.Thread(target=run, daemon=True).start()
        return {"status": "started"}

    @router.get("/api/youtube/upload/status")
    def upload_status():
        return state.upl.snapshot()

    @router.post("/api/youtube/upload/cancel")
    def cancel_upload():
        state.upl.cancelled = True
        return {"status": "cancelling"}

    return router
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/routes/youtube.py
git commit -m "refactor: extract YouTube endpoints to routes/youtube.py"
```

---

### Task 11: Rewrite web.py as thin factory and run full tests

**Files:**
- Modify: `clipcutter/web.py`

- [ ] **Step 1: Replace web.py content**

Keep `_cleanup_stale_pending` in web.py (it is a startup concern). All state classes, models, and route functions are now in their respective modules.

```python
"""Web UI for clip processing and review."""
import json
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
```

- [ ] **Step 2: Run the full test suite**

```bash
cd E:/Projects/ClipCutter && pytest tests/ -v -k "not browser"
```
Expected: all API tests pass. Fix any import errors before continuing.

- [ ] **Step 3: Run browser tests**

```bash
pytest tests/ -v
```
Expected: all 34 tests pass.

- [ ] **Step 4: Commit**

```bash
git add clipcutter/web.py
git commit -m "refactor: web.py is now a thin factory mounting 5 APIRouters"
```

---

## Phase 2: Architecture Refactor — Frontend

### Task 12: Initialize Vite project

**Files:**
- Create: `clipcutter/static/package.json`
- Create: `clipcutter/static/vite.config.ts`
- Create: `clipcutter/static/tsconfig.json`

- [ ] **Step 1: Create package.json**

```json
{
  "name": "clipcutter-ui",
  "private": true,
  "version": "1.0.0",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "devDependencies": {
    "typescript": "^5.4.0",
    "vite": "^5.2.0"
  }
}
```

- [ ] **Step 2: Create vite.config.ts**

```typescript
import { defineConfig } from 'vite';

export default defineConfig({
  root: '.',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/video': 'http://localhost:8000',
      '/oauth': 'http://localhost:8000',
    },
  },
});
```

- [ ] **Step 3: Create tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "module": "ESNext",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Install dependencies**

```bash
cd E:/Projects/ClipCutter/clipcutter/static && npm install
```
Expected: `node_modules/` created, `package-lock.json` written.

- [ ] **Step 5: Add to .gitignore**

Add to `.gitignore` in the project root:
```
clipcutter/static/node_modules/
clipcutter/static/dist/
```

- [ ] **Step 6: Create src/ directory structure**

```bash
mkdir -p E:/Projects/ClipCutter/clipcutter/static/src/tabs
```

- [ ] **Step 7: Commit**

```bash
cd E:/Projects/ClipCutter
git add clipcutter/static/package.json clipcutter/static/vite.config.ts clipcutter/static/tsconfig.json clipcutter/static/package-lock.json .gitignore
git commit -m "build: add Vite + TypeScript setup for frontend"
```

---

### Task 13: Create src/utils.ts

**Files:**
- Create: `clipcutter/static/src/utils.ts`

- [ ] **Step 1: Create utils.ts**

```typescript
/** Format seconds as MM:SS */
export function fmtTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}

/** Format seconds as M:SS.d (tenths precision) */
export function fmtTimePrecise(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.round((seconds % 1) * 10);
  return `${m}:${String(s).padStart(2, '0')}.${ms}`;
}

/** Parse "M:SS.d" or plain seconds string into a number */
export function parseTrimTime(str: string): number {
  const parts = str.trim().split(':');
  if (parts.length === 2) {
    return parseInt(parts[0]) * 60 + parseFloat(parts[1]);
  }
  return parseFloat(str) || 0;
}

/** Escape HTML special characters */
export function escapeHtml(text: string): string {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}

/** Convert display name to safe filename stem */
export function sanitizeFilename(name: string): string {
  return name.replace(/[^a-zA-Z0-9 _-]/g, '').trim().replace(/ /g, '_');
}

/** Format a raw filename into a title (remove extension, replace _ and - with spaces) */
export function formatClipTitle(filename: string): string {
  let title = filename.replace(/\.[^.]+$/, '');
  title = title.replace(/[_-]/g, ' ');
  return title.charAt(0).toUpperCase() + title.slice(1);
}
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/static/src/utils.ts
git commit -m "feat: add frontend utils.ts module"
```

---

### Task 14: Create src/api.ts

**Files:**
- Create: `clipcutter/static/src/api.ts`

- [ ] **Step 1: Create api.ts with all types and fetch wrappers**

```typescript
// ---- Shared types ----

export interface HighlightRegion {
  offset: number;
  duration: number;
  type: string;
}

export interface ClipInfo {
  filename: string;
  source_video: string;
  video_stem: string;
  start_time: number;
  end_time: number;
  duration: number;
  detection_reasons: string[];
  confidence: number;
  video_url: string;
  highlight_regions: HighlightRegion[];
}

export interface KeptClipInfo {
  filename: string;
  source_video: string;
  video_stem: string;
  start_time: number;
  end_time: number;
  duration: number;
  detection_reasons: string[];
  confidence: number;
  video_url: string;
  custom_name: string | null;
  encoded_filename: string | null;
  encoding_preset: string | null;
  encoded_exists: boolean;
  encoded_video_url?: string;
  youtube_video_id: string | null;
  youtube_url: string | null;
  youtube_upload_status: string | null;
}

export interface WaveformData {
  waveform: number[];
  duration: number;
  sample_count: number;
  highlight_regions: HighlightRegion[];
}

export interface KeepRequest {
  trim_start: number;
  trim_end: number;
  needs_trim: boolean;
  custom_name: string | null;
}

export interface ProcessStatus {
  running: boolean;
  log: string[];
  error: string | null;
}

export interface EncodeStatus {
  running: boolean;
  current_file: string | null;
  current_index: number;
  total: number;
  completed: string[];
  errors: Array<{ filename: string; error: string }>;
  cancelled: boolean;
}

export interface UploadStatus {
  running: boolean;
  current_file: string | null;
  current_index: number;
  total: number;
  bytes_sent: number;
  bytes_total: number;
  completed: Array<{ filename: string; video_id: string; url: string }>;
  errors: Array<{ filename: string; error: string }>;
  cancelled: boolean;
}

export interface CompilationStatus {
  running: boolean;
  current_step: string;
  progress_pct: number;
  completed: boolean;
  error: string | null;
  output_filename: string | null;
  cancelled: boolean;
}

export interface CompilationInfo {
  compilation_id: string;
  filename: string;
  title: string | null;
  clips: Array<{ video_stem: string; filename: string; custom_name?: string }>;
  total_duration?: number;
  clip_count: number;
  file_exists: boolean;
}

export interface PresetInfo {
  name: string;
  display_name: string;
  extension: string;
}

export interface PresetsData {
  presets: PresetInfo[];
  default: string;
  fps_options: Array<number | null>;
}

export interface SourceVideo {
  video_stem: string;
  source_path: string;
  exists: boolean;
  size_mb: number;
  fully_reviewed: boolean;
  kept: number;
  discarded: number;
  total: number;
}

export interface YouTubeStatus {
  authenticated: boolean;
  channel_name?: string;
  error?: string;
}

export interface Playlist {
  id: string;
  title: string;
  item_count: number;
}

export interface EncodeClipRef {
  video_stem: string;
  filename: string;
}

export interface CompilationClipRef {
  video_stem: string;
  filename: string;
}

export interface YouTubeUploadClip {
  video_stem: string;
  filename: string;
  use_encoded: boolean;
  title: string;
  description: string;
  tags: string[];
  category_id: string;
  privacy: string;
  playlist_id: string | null;
}

// ---- API helpers ----

async function apiPost<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json() as Promise<T>;
}

async function apiGet<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json() as Promise<T>;
}

async function apiDelete(url: string): Promise<void> {
  const res = await fetch(url, { method: 'DELETE' });
  if (!res.ok) throw new Error(res.statusText);
}

// ---- Process ----

export const fetchDefaults = () => apiGet<{ folder: string }>('/api/defaults');
export const startProcessing = (body: { folder: string; sensitivity: number; context: number | null }) =>
  apiPost<{ status: string }>('/api/process', body);
export const fetchProcessStatus = () => apiGet<ProcessStatus>('/api/process/status');

// ---- Review ----

export const fetchClips = () => apiGet<{ clips: ClipInfo[]; total: number }>('/api/clips');
export const fetchWaveform = (stem: string, filename: string) =>
  apiGet<WaveformData>(`/api/waveform/${stem}/${filename}`);
export const keepClip = (stem: string, filename: string, req: KeepRequest) =>
  apiPost<{ status: string; trimmed: boolean }>(`/api/clips/${stem}/${filename}/keep`, req);
export const discardClip = (stem: string, filename: string) =>
  apiPost<{ status: string }>(`/api/clips/${stem}/${filename}/discard`, {});

// ---- Sources ----

export const fetchSources = () => apiGet<{ sources: SourceVideo[] }>('/api/sources');
export const deleteSource = (videoStem: string) =>
  apiPost<{ status: string; freed_mb: number; leftover: number }>(`/api/sources/${videoStem}/delete`, {});

// ---- Encode ----

export const fetchPresets = () => apiGet<PresetsData>('/api/encoding/presets');
export const fetchKeptClips = () => apiGet<{ clips: KeptClipInfo[]; total: number }>('/api/kept');
export const startEncoding = (body: { clips: EncodeClipRef[]; preset: string; target_fps: number | null; slowdown_factor: number | null }) =>
  apiPost<{ status: string }>('/api/encode', body);
export const fetchEncodeStatus = () => apiGet<EncodeStatus>('/api/encode/status');
export const cancelEncoding = () => apiPost<{ status: string }>('/api/encode/cancel', {});

// ---- Compile ----

export const startCompilation = (body: { clips: CompilationClipRef[]; transition: string; crossfade_duration: number; preset?: string; title?: string }) =>
  apiPost<{ status: string; compilation_id: string }>('/api/compilation', body);
export const fetchCompilationStatus = () => apiGet<CompilationStatus>('/api/compilation/status');
export const cancelCompilation = () => apiPost<{ status: string }>('/api/compilation/cancel', {});
export const fetchCompilations = () => apiGet<{ compilations: CompilationInfo[] }>('/api/compilations');
export const deleteCompilation = (compId: string) => apiDelete(`/api/compilation/${compId}`);

// ---- YouTube ----

export const fetchYouTubeStatus = () => apiGet<YouTubeStatus>('/api/youtube/status');
export const fetchYouTubePlaylists = () => apiGet<{ playlists: Playlist[] }>('/api/youtube/playlists');
export const startYouTubeAuth = (clientId: string, clientSecret: string) =>
  apiPost<{ auth_url: string }>('/api/youtube/auth/start', { client_id: clientId, client_secret: clientSecret });
export const revokeYouTubeAuth = () => apiPost<{ status: string }>('/api/youtube/auth/revoke', {});
export const startUpload = (body: { clips: YouTubeUploadClip[] }) =>
  apiPost<{ status: string }>('/api/youtube/upload', body);
export const fetchUploadStatus = () => apiGet<UploadStatus>('/api/youtube/upload/status');
export const cancelUpload = () => apiPost<{ status: string }>('/api/youtube/upload/cancel', {});
export const createPlaylist = (title: string, privacy: string) =>
  apiPost<Playlist>('/api/youtube/playlists', { title, privacy });
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/static/src/api.ts
git commit -m "feat: add typed api.ts with all fetch wrappers"
```

---

### Task 15: Create src/waveform.ts

**Files:**
- Create: `clipcutter/static/src/waveform.ts`

- [ ] **Step 1: Create waveform.ts**

```typescript
import type { HighlightRegion, WaveformData } from './api';
import { fetchWaveform } from './api';

const REGION_COLORS: Record<string, string> = {
  volume_spike: '#f87171',
  laughter: '#4ade80',
  shouting: '#fbbf24',
  sudden_noise: '#60a5fa',
  fallback: '#888',
};

let waveformData: WaveformData | null = null;
let animFrame: number | null = null;

export async function loadWaveform(
  videoStem: string,
  filename: string,
  fallbackRegions: HighlightRegion[],
): Promise<void> {
  waveformData = null;
  stopWaveformSync();
  try {
    const data = await fetchWaveform(videoStem, filename);
    waveformData = data;
    if (!waveformData.highlight_regions || waveformData.highlight_regions.length === 0) {
      waveformData.highlight_regions = fallbackRegions;
    }
    renderWaveform();
    startWaveformSync();
  } catch (e) {
    console.error('Waveform load failed:', e);
  }
}

export function renderWaveform(): void {
  const canvas = document.getElementById('waveformCanvas') as HTMLCanvasElement | null;
  if (!canvas || !waveformData) return;
  const ctx = canvas.getContext('2d')!;

  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;
  ctx.scale(devicePixelRatio, devicePixelRatio);
  const w = rect.width;
  const h = rect.height;
  ctx.clearRect(0, 0, w, h);

  const bars = waveformData.waveform;
  const dur = waveformData.duration;
  const regions = waveformData.highlight_regions || [];

  for (const region of regions) {
    const x1 = (region.offset / dur) * w;
    const x2 = ((region.offset + region.duration) / dur) * w;
    ctx.fillStyle = REGION_COLORS[region.type] || '#fbbf24';
    ctx.globalAlpha = 0.12;
    ctx.fillRect(x1, 0, Math.max(x2 - x1, 2), h);
  }
  ctx.globalAlpha = 1.0;

  const barWidth = w / bars.length;
  const gap = Math.max(0.5, barWidth * 0.15);
  for (let i = 0; i < bars.length; i++) {
    const val = bars[i];
    const barH = Math.max(1, val * h * 0.9);
    const x = i * barWidth;
    const y = (h - barH) / 2;
    const barTime = (i / bars.length) * dur;
    let inRegion = false;
    for (const region of regions) {
      if (barTime >= region.offset && barTime <= region.offset + region.duration) {
        ctx.fillStyle = REGION_COLORS[region.type] || '#fbbf24';
        inRegion = true;
        break;
      }
    }
    if (!inRegion) ctx.fillStyle = '#3b82f6';
    ctx.globalAlpha = 0.85;
    ctx.fillRect(x + gap / 2, y, barWidth - gap, barH);
  }
  ctx.globalAlpha = 1.0;
}

export function startWaveformSync(): void {
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (!player) return;

  function tick() {
    const cursor = document.getElementById('waveformCursor');
    if (cursor && player!.duration) {
      cursor.style.left = (player!.currentTime / player!.duration) * 100 + '%';
    }
    animFrame = requestAnimationFrame(tick);
  }
  animFrame = requestAnimationFrame(tick);
}

export function stopWaveformSync(): void {
  if (animFrame !== null) {
    cancelAnimationFrame(animFrame);
    animFrame = null;
  }
}

export function updateWaveformTrimMarkers(inPct: number, outPct: number, hasTrim: boolean): void {
  const trimIn = document.getElementById('waveformTrimIn');
  const trimOut = document.getElementById('waveformTrimOut');
  if (!trimIn || !trimOut) return;
  if (hasTrim) {
    trimIn.style.display = 'block';
    trimIn.style.left = inPct + '%';
    trimOut.style.display = 'block';
    trimOut.style.left = outPct + '%';
  } else {
    trimIn.style.display = 'none';
    trimOut.style.display = 'none';
  }
}

export function getWaveformDuration(): number {
  return waveformData?.duration ?? 0;
}
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/static/src/waveform.ts
git commit -m "feat: add waveform.ts module extracted from index.html"
```

---

### Task 16: Create src/tabs/process.ts

**Files:**
- Create: `clipcutter/static/src/tabs/process.ts`

- [ ] **Step 1: Create process.ts**

```typescript
import { fetchDefaults, startProcessing, fetchProcessStatus } from '../api';
import { escapeHtml } from '../utils';

let pollTimer: ReturnType<typeof setInterval> | null = null;

export async function initProcessTab(): Promise<void> {
  try {
    const data = await fetchDefaults();
    const folderInput = document.getElementById('folderPath') as HTMLInputElement | null;
    if (folderInput) folderInput.value = data.folder;
  } catch (e) {
    console.error('Failed to load defaults:', e);
  }
}

export async function startProcessingHandler(): Promise<void> {
  const folderInput = document.getElementById('folderPath') as HTMLInputElement | null;
  const folder = folderInput?.value.trim() ?? '';
  if (!folder) { alert('Enter a folder path.'); return; }

  const sensitivity = parseFloat((document.getElementById('sensitivity') as HTMLInputElement).value) || 1.0;
  const ctxVal = (document.getElementById('context') as HTMLInputElement).value.trim();
  const context = ctxVal ? parseFloat(ctxVal) : null;

  const btn = document.getElementById('btnProcess') as HTMLButtonElement;
  btn.disabled = true;
  btn.textContent = 'Processing...';
  const logBox = document.getElementById('logBox')!;
  logBox.innerHTML = '';

  try {
    await startProcessing({ folder, sensitivity, context });
    pollTimer = setInterval(pollStatus, 800);
  } catch (e) {
    alert('Error: ' + (e as Error).message);
    btn.disabled = false;
    btn.textContent = 'Process';
  }
}

async function pollStatus(): Promise<void> {
  try {
    const data = await fetchProcessStatus();
    const box = document.getElementById('logBox')!;
    box.innerHTML = data.log.map(line => `<div class="log-line">${escapeHtml(line)}</div>`).join('');
    box.scrollTop = box.scrollHeight;

    if (!data.running) {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      const btn = document.getElementById('btnProcess') as HTMLButtonElement;
      btn.disabled = false;
      btn.textContent = 'Process';

      if (data.error) {
        box.innerHTML += `<div class="log-line log-error">Error: ${escapeHtml(data.error)}</div>`;
      } else {
        box.innerHTML += `<div class="log-line log-done">Done! Switch to Review tab to review clips.</div>`;
      }
      box.scrollTop = box.scrollHeight;
    }
  } catch (e) {
    console.error('Poll error:', e);
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/static/src/tabs/process.ts
git commit -m "feat: add tabs/process.ts module"
```

---

### Task 17: Create src/tabs/review.ts

**Files:**
- Create: `clipcutter/static/src/tabs/review.ts`

- [ ] **Step 1: Create review.ts**

```typescript
import { fetchClips, keepClip, discardClip, fetchSources, deleteSource } from '../api';
import type { ClipInfo } from '../api';
import { fmtTime, fmtTimePrecise, parseTrimTime, escapeHtml } from '../utils';
import { loadWaveform, stopWaveformSync, updateWaveformTrimMarkers, getWaveformDuration } from '../waveform';

let clips: ClipInfo[] = [];
let currentIndex = 0;
let results: Array<string | null> = [];
export let savedVolume = 0.5;

export async function loadClips(): Promise<void> {
  const data = await fetchClips();
  clips = data.clips;
  results = new Array(clips.length).fill(null);
  currentIndex = 0;
  clips.length === 0 ? showEmpty() : showClip();
}

function showEmpty(): void {
  const hdr = document.getElementById('headerRight');
  if (hdr) hdr.textContent = '';
  document.getElementById('reviewContent')!.innerHTML =
    '<div class="empty-state">No pending clips. Process some videos first.</div>';
}

function showClip(): void {
  if (currentIndex >= clips.length) { showDone(); return; }

  const clip = clips[currentIndex];
  const hdr = document.getElementById('headerRight');
  if (hdr) hdr.textContent = `Clip ${currentIndex + 1} of ${clips.length}`;

  const confPercent = Math.round(clip.confidence * 100);
  const confColor = clip.confidence > 0.7 ? '#4ade80' : clip.confidence > 0.4 ? '#fbbf24' : '#f87171';
  const tags = clip.detection_reasons.map(r =>
    `<span class="tag tag-${r}">${r.replace('_', ' ')}</span>`
  ).join('');
  const sourceName = clip.source_video.split(/[/\\]/).pop() ?? clip.source_video;

  document.getElementById('reviewContent')!.innerHTML = `
    <div class="progress">
      ${clips.map((_, i) => {
        let cls = 'progress-dot';
        if (i < currentIndex) cls += results[i] === 'discarded' ? ' discarded' : ' done';
        else if (i === currentIndex) cls += ' current';
        return `<div class="${cls}"></div>`;
      }).join('')}
    </div>
    <div class="player-section">
      <video id="player" controls autoplay onvolumechange="window._savedVol=this.volume"
             onloadeddata="this.volume=window._savedVol||0.5">
        <source src="${clip.video_url}" type="video/mp4">
      </video>
      <div class="waveform-container" id="waveformContainer">
        <canvas class="waveform-canvas" id="waveformCanvas"></canvas>
        <div class="waveform-cursor" id="waveformCursor" style="left:0"></div>
        <div class="waveform-dimmed" id="waveformDimLeft" style="left:0;width:0"></div>
        <div class="waveform-dimmed" id="waveformDimRight" style="right:0;width:0"></div>
        <div class="waveform-trim-marker waveform-trim-in" id="waveformTrimIn" style="left:0;display:none"></div>
        <div class="waveform-trim-marker waveform-trim-out" id="waveformTrimOut" style="left:0;display:none"></div>
      </div>
      <div class="clip-info">
        <div class="clip-title">${escapeHtml(sourceName)}</div>
        <div class="clip-meta">
          <span>${fmtTime(clip.start_time)} - ${fmtTime(clip.end_time)}</span>
          <span>${Math.round(clip.duration)}s</span>
          <span>Confidence: ${confPercent}%</span>
        </div>
        <div class="tags">${tags}</div>
        <div class="confidence-bar">
          <div class="confidence-fill" style="width:${confPercent}%;background:${confColor}"></div>
        </div>
      </div>
      <div class="trim-section">
        <div class="trim-group">
          <span class="trim-label">In</span>
          <input type="text" class="trim-time" id="trimIn" value="0:00" />
          <button class="trim-btn" onclick="window._cc.setTrimPoint('in')">Set</button>
          <button class="trim-btn" onclick="window._cc.seekTo('in')">Go</button>
        </div>
        <div class="trim-group">
          <span class="trim-label">Out</span>
          <input type="text" class="trim-time" id="trimOut" value="${fmtTimePrecise(clip.duration)}" />
          <button class="trim-btn" onclick="window._cc.setTrimPoint('out')">Set</button>
          <button class="trim-btn" onclick="window._cc.seekTo('out')">Go</button>
        </div>
        <span class="trim-indicator" id="trimIndicator"></span>
      </div>
      <div class="name-section">
        <span class="trim-label">Name</span>
        <input type="text" class="clip-name-input" id="clipCustomName" placeholder="Optional custom name for export..." />
      </div>
      <div class="actions">
        <button class="btn btn-keep" onclick="window._cc.clipAction('keep')">Keep <span class="shortcut">K</span></button>
        <button class="btn btn-skip" onclick="window._cc.clipAction('skip')">Skip <span class="shortcut">S</span></button>
        <button class="btn btn-discard" onclick="window._cc.clipAction('discard')">Discard <span class="shortcut">D</span></button>
      </div>
    </div>
    <div class="keyboard-hint">
      <kbd>K</kbd> keep &nbsp; <kbd>S</kbd> skip &nbsp; <kbd>D</kbd> discard &nbsp;
      <kbd>Space</kbd> play/pause &nbsp; <kbd>I</kbd> set in &nbsp; <kbd>O</kbd> set out &nbsp;
      <kbd>N</kbd> focus name
    </div>
  `;

  loadWaveform(clip.video_stem, clip.filename, clip.highlight_regions || []);

  // Waveform click-to-seek
  const container = document.getElementById('waveformContainer');
  if (container) {
    container.addEventListener('click', (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!container.contains(target) || target.tagName === 'INPUT') return;
      const player = document.getElementById('player') as HTMLVideoElement | null;
      if (!player || !player.duration) return;
      const rect = container.getBoundingClientRect();
      player.currentTime = ((e.clientX - rect.left) / rect.width) * player.duration;
    });
  }
}

export function setTrimPoint(which: 'in' | 'out'): void {
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (!player) return;
  const input = document.getElementById(which === 'in' ? 'trimIn' : 'trimOut') as HTMLInputElement | null;
  if (input) {
    input.value = fmtTimePrecise(player.currentTime);
    updateTrimIndicator();
  }
}

export function seekTo(which: 'in' | 'out'): void {
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (!player) return;
  const input = document.getElementById(which === 'in' ? 'trimIn' : 'trimOut') as HTMLInputElement | null;
  if (input) player.currentTime = parseTrimTime(input.value);
}

export function updateTrimIndicator(): void {
  const clip = clips[currentIndex];
  if (!clip) return;
  const inVal = parseTrimTime((document.getElementById('trimIn') as HTMLInputElement).value);
  const outVal = parseTrimTime((document.getElementById('trimOut') as HTMLInputElement).value);
  const indicator = document.getElementById('trimIndicator')!;
  const trimmed = inVal > 0.1 || (clip.duration - outVal) > 0.1;
  if (trimmed) {
    indicator.textContent = `Trimmed: ${Math.max(0, outVal - inVal).toFixed(1)}s`;
    indicator.className = 'trim-indicator active';
  } else {
    indicator.textContent = '';
    indicator.className = 'trim-indicator';
  }

  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (player && player.duration) {
    const dur = player.duration;
    updateWaveformTrimMarkers(
      (inVal / dur) * 100,
      (outVal / dur) * 100,
      trimmed,
    );
  }
}

export async function clipAction(type: 'keep' | 'skip' | 'discard'): Promise<void> {
  const clip = clips[currentIndex];
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (player) player.pause();

  if (type === 'keep') {
    const trimStart = parseTrimTime((document.getElementById('trimIn') as HTMLInputElement)?.value || '0');
    const trimEnd = parseTrimTime((document.getElementById('trimOut') as HTMLInputElement)?.value || '0');
    const customName = (document.getElementById('clipCustomName') as HTMLInputElement)?.value?.trim() || null;
    const needsTrim = trimStart > 0.1 || (clip.duration - trimEnd) > 0.1;

    showOverlay(needsTrim ? 'Trimming clip...' : 'Saving clip...');
    try {
      await keepClip(clip.video_stem, clip.filename, {
        trim_start: trimStart,
        trim_end: trimEnd,
        needs_trim: needsTrim,
        custom_name: customName,
      });
    } catch (e) {
      hideOverlay();
      alert((e as Error).message || 'Keep failed');
      return;
    }
    hideOverlay();
    results[currentIndex] = 'kept';
  } else if (type === 'discard') {
    await discardClip(clip.video_stem, clip.filename).catch(() => {});
    results[currentIndex] = 'discarded';
  } else {
    results[currentIndex] = 'skipped';
  }

  currentIndex++;
  showClip();
}

function showOverlay(text: string): void {
  const el = document.getElementById('overlayText');
  const overlay = document.getElementById('overlay');
  if (el) el.textContent = text;
  if (overlay) overlay.classList.add('active');
}

function hideOverlay(): void {
  document.getElementById('overlay')?.classList.remove('active');
}

async function showDone(): Promise<void> {
  const kept = results.filter(r => r === 'kept').length;
  const discarded = results.filter(r => r === 'discarded').length;
  const skipped = results.filter(r => r === 'skipped').length;

  const hdr = document.getElementById('headerRight');
  if (hdr) hdr.textContent = 'Done';

  let html = `
    <div class="done-state">
      <h2>Review Complete</h2>
      <p>${kept} kept &middot; ${discarded} discarded &middot; ${skipped} skipped</p>
    </div>
  `;

  try {
    const data = await fetchSources();
    const deletable = data.sources.filter(s => s.fully_reviewed && s.exists);
    if (deletable.length > 0) {
      const totalMb = deletable.reduce((sum, s) => sum + s.size_mb, 0);
      html += `<div class="cleanup-section"><h3>Delete Original Videos</h3>`;
      html += `<p style="color:#888;font-size:13px;margin-bottom:16px;">Fully reviewed. Delete to free disk space.</p>`;
      for (const src of deletable) {
        const name = src.source_path.split(/[/\\]/).pop() ?? src.source_path;
        html += `
          <div class="source-row" id="src-${src.video_stem}">
            <div class="source-info">
              <div class="source-name" title="${escapeHtml(src.source_path)}">${escapeHtml(name)}</div>
              <div class="source-detail">${src.kept} kept, ${src.discarded} discarded</div>
            </div>
            <div class="source-size">${src.size_mb} MB</div>
            <button class="btn-delete" onclick="window._cc.deleteSourceHandler('${src.video_stem}', this)">Delete</button>
          </div>`;
      }
      html += `<div class="cleanup-total">Total reclaimable: ${totalMb.toFixed(1)} MB</div></div>`;
    }
  } catch (e) {
    console.error('Failed to load sources:', e);
  }

  document.getElementById('reviewContent')!.innerHTML = html;
}

export async function deleteSourceHandler(videoStem: string, btn: HTMLButtonElement): Promise<void> {
  if (!confirm('Permanently delete this source video? This cannot be undone.')) return;
  btn.disabled = true;
  btn.textContent = 'Deleting...';
  try {
    const data = await deleteSource(videoStem);
    const row = document.getElementById('src-' + videoStem)!;
    row.querySelector('.btn-delete')!.outerHTML = '<button class="btn-deleted">Deleted</button>';
    const sizeEl = row.querySelector('.source-size') as HTMLElement;
    let label = data.freed_mb + ' MB freed';
    if (data.leftover > 0) label += ` (${data.leftover} clip(s) locked, will clean on restart)`;
    sizeEl.textContent = label;
    sizeEl.style.color = data.leftover > 0 ? '#fbbf24' : '#4ade80';
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false;
    btn.textContent = 'Delete';
  }
}

export { stopWaveformSync };
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/static/src/tabs/review.ts
git commit -m "feat: add tabs/review.ts module"
```

---

### Task 18: Create src/tabs/encode.ts

**Files:**
- Create: `clipcutter/static/src/tabs/encode.ts`

- [ ] **Step 1: Create encode.ts**

```typescript
import {
  fetchKeptClips, fetchPresets, startEncoding, fetchEncodeStatus, cancelEncoding,
  fetchYouTubeStatus, fetchYouTubePlaylists, startYouTubeAuth, revokeYouTubeAuth,
  startUpload, fetchUploadStatus, cancelUpload, createPlaylist,
} from '../api';
import type { KeptClipInfo, Playlist } from '../api';
import { escapeHtml, fmtTime, formatClipTitle } from '../utils';

export let keptClips: KeptClipInfo[] = [];
let encodingPresets: Array<{ name: string; display_name: string; extension: string }> = [];
let defaultPreset = 'h264_hq';
let encodingFpsOptions: Array<number | null> = [null, 24, 30, 60];
let ytAuthenticated = false;
let ytChannelName = '';
let ytPlaylists: Playlist[] = [];

let encodingPoll: ReturnType<typeof setInterval> | null = null;
let uploadPoll: ReturnType<typeof setInterval> | null = null;

export async function loadExportTab(): Promise<void> {
  try {
    const [keptData, presetsData, ytStatus] = await Promise.all([
      fetchKeptClips(),
      fetchPresets(),
      fetchYouTubeStatus(),
    ]);
    keptClips = keptData.clips || [];
    encodingPresets = presetsData.presets || [];
    defaultPreset = presetsData.default || 'h264_hq';
    encodingFpsOptions = presetsData.fps_options || [null, 24, 30, 60];
    ytAuthenticated = ytStatus.authenticated || false;
    ytChannelName = ytStatus.channel_name || '';

    if (ytAuthenticated) {
      try {
        const plData = await fetchYouTubePlaylists();
        ytPlaylists = plData.playlists || [];
      } catch { ytPlaylists = []; }
    }
  } catch (e) {
    console.error('Failed to load export tab:', e);
  }
  renderExportView();
}

export function renderExportView(): void {
  let html = '';

  // === Encode Clips ===
  html += `<div class="export-section"><h2>Encode Clips</h2>`;
  html += `<div class="export-toolbar"><div class="form-group">`;
  html += `<label>Preset</label><select class="select-styled" id="encodePreset">`;
  for (const p of encodingPresets) {
    html += `<option value="${p.name}" ${p.name === defaultPreset ? 'selected' : ''}>${escapeHtml(p.display_name)}</option>`;
  }
  html += `</select></div><div class="form-group">`;
  html += `<label>FPS</label><select class="select-styled" id="encodeFps" style="width:100px">`;
  for (const fps of encodingFpsOptions) {
    html += `<option value="${fps ?? ''}">${fps ? fps + 'fps' : 'Original'}</option>`;
  }
  html += `</select></div>`;
  html += `<div class="form-group" id="slowdownGroup" style="display:none">`;
  html += `<label>Slowdown</label><select class="select-styled" id="encodeSlowdown" style="width:100px">`;
  html += `<option value="">None</option><option value="0.5">0.5x</option><option value="0.25">0.25x</option>`;
  html += `</select></div>`;
  html += `<button class="btn-secondary" onclick="window._cc.toggleAllClips('encode')">Select All</button>`;
  html += `<button class="btn-process" id="btnEncode" onclick="window._cc.startEncodingHandler()">Encode Selected</button></div>`;
  html += `<div id="encodeProgress" style="display:none"><div style="display:flex;align-items:center;gap:12px">`;
  html += `<span class="progress-label" id="encodeProgressLabel"></span>`;
  html += `<button class="btn-cancel" onclick="window._cc.cancelEncodingHandler()">Cancel</button></div>`;
  html += `<div class="progress-bar"><div class="progress-fill" id="encodeProgressFill"></div></div></div>`;

  if (keptClips.length === 0) {
    html += `<div class="empty-state">No kept clips yet.</div>`;
  } else {
    html += `<div class="clip-list">`;
    for (let i = 0; i < keptClips.length; i++) {
      const clip = keptClips[i];
      const dur = clip.duration ? Math.round(clip.duration) + 's' : '';
      const tags = (clip.detection_reasons || []).map(r =>
        `<span class="tag tag-${r}">${r.replace('_', ' ')}</span>`
      ).join('');
      const badge = clip.encoded_exists ? `<span class="badge badge-encoded">Encoded</span>` : '';
      html += `<div class="clip-row">`;
      html += `<input type="checkbox" class="clip-checkbox encode-cb" data-index="${i}" checked>`;
      html += `<span class="clip-name" title="${escapeHtml(clip.filename)}">${escapeHtml(clip.custom_name || clip.filename)}</span>`;
      html += `<span class="clip-detail">${escapeHtml(clip.video_stem || '')}</span>`;
      html += `<span class="clip-detail">${dur}</span>`;
      html += `<span class="tags" style="margin-bottom:0">${tags}</span>`;
      html += badge;
      html += `</div>`;
    }
    html += `</div>`;
  }
  html += `</div>`;

  // === Build Compilation ===
  if (keptClips.length >= 2) {
    html += `<div class="export-section"><h2>Build Compilation</h2>`;
    html += `<p style="color:#888;font-size:13px;margin-bottom:16px">Select clips above, then build a highlight reel. Drag to reorder.</p>`;
    html += `<div class="comp-toolbar">`;
    html += `<button class="btn-secondary" onclick="window._cc.addSelectedToCompilation()">Add Selected</button>`;
    html += `<div class="form-group"><label>Transition</label>`;
    html += `<select class="select-styled" id="compTransition" onchange="window._cc.updateCompDuration()">`;
    html += `<option value="cut">Hard Cut</option><option value="crossfade">Crossfade</option></select></div>`;
    html += `<div class="form-group" id="compXfadeGroup" style="display:none"><label>Duration</label>`;
    html += `<input type="number" class="trim-time" id="compXfadeDur" value="0.5" min="0.1" max="3" step="0.1" style="width:70px" oninput="window._cc.updateCompDuration()">`;
    html += `<span style="color:#888;font-size:12px">s</span></div>`;
    html += `<div class="form-group"><label>Title</label>`;
    html += `<input type="text" class="clip-name-input" id="compTitle" placeholder="Optional title..." style="width:200px"></div>`;
    html += `</div>`;
    html += `<div id="compList"></div>`;
    html += `<div class="comp-footer">`;
    html += `<span class="comp-summary" id="compSummary">No clips added yet.</span>`;
    html += `<button class="btn-process" id="btnBuildComp" onclick="window._cc.startCompilationHandler()">Build</button>`;
    html += `<button class="btn-cancel" onclick="window._cc.cancelCompilationHandler()">Cancel</button>`;
    html += `</div>`;
    html += `<div id="compProgress" style="display:none">`;
    html += `<div style="display:flex;align-items:center;gap:12px">`;
    html += `<span class="progress-label" id="compProgressLabel"></span></div>`;
    html += `<div class="progress-bar"><div class="progress-fill" id="compProgressFill"></div></div></div>`;
    html += `<div id="pastCompilations"></div></div>`;
  }

  // === YouTube Upload ===
  html += `<div class="export-section"><h2>Upload to YouTube</h2>`;
  if (!ytAuthenticated) {
    html += `<div class="yt-auth-section">`;
    html += `<div class="form-group"><label>Client ID</label><input type="text" id="ytClientId" placeholder="OAuth Client ID"></div>`;
    html += `<div class="form-group"><label>Client Secret</label><input type="password" id="ytClientSecret" placeholder="OAuth Client Secret"></div>`;
    html += `<button class="btn-process" onclick="window._cc.startYouTubeAuthHandler()">Sign In</button></div>`;
    html += `<p class="yt-help">Create OAuth credentials at <a href="https://console.cloud.google.com" target="_blank" style="color:#60a5fa">console.cloud.google.com</a></p>`;
  } else {
    html += `<div class="yt-connected">`;
    html += `<span style="color:#888;font-size:13px">Connected as:</span>`;
    html += `<span class="yt-channel">${escapeHtml(ytChannelName)}</span>`;
    html += `<button class="btn-signout" onclick="window._cc.revokeYouTubeAuthHandler()">Sign Out</button></div>`;
    html += `<div class="yt-settings">`;
    html += `<div class="form-group"><label>Privacy</label><select class="select-styled" id="ytPrivacy">`;
    html += `<option value="private" selected>Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></div>`;
    html += `<div class="form-group"><label>Playlist</label><div class="playlist-row"><select class="select-styled" id="ytPlaylist">`;
    html += `<option value="">None</option>`;
    for (const pl of ytPlaylists) {
      html += `<option value="${escapeHtml(pl.id)}">${escapeHtml(pl.title)} (${pl.item_count})</option>`;
    }
    html += `<option value="__create__">+ Create New...</option></select></div></div>`;
    html += `<div class="form-group"><label>Category</label><select class="select-styled" id="ytCategory">`;
    html += `<option value="20" selected>Gaming</option><option value="24">Entertainment</option>`;
    html += `<option value="22">People &amp; Blogs</option><option value="17">Sports</option>`;
    html += `<option value="10">Music</option><option value="1">Film &amp; Animation</option><option value="23">Comedy</option>`;
    html += `</select></div>`;
    html += `<div class="form-group"><label>Tags</label><input type="text" class="title-input" id="ytTags" placeholder="tag1, tag2, tag3" style="width:100%"></div>`;
    html += `<div class="form-group full-width"><label>Description Template</label>`;
    html += `<textarea class="textarea-styled" id="ytDescription" placeholder="Enter a description template..."></textarea>`;
    html += `<div class="template-vars">Variables: <code>{source_video}</code> <code>{start_time}</code> <code>{end_time}</code> <code>{duration}</code> <code>{detection_reasons}</code></div>`;
    html += `</div></div>`;
    html += `<div class="export-toolbar">`;
    html += `<button class="btn-secondary" onclick="window._cc.toggleAllClips('upload')">Select All</button>`;
    html += `<button class="btn-process" id="btnUpload" onclick="window._cc.startUploadHandler()">Upload Selected</button></div>`;
    html += `<div id="uploadProgress" style="display:none">`;
    html += `<div style="display:flex;align-items:center;gap:12px">`;
    html += `<span class="progress-label" id="uploadProgressLabel"></span>`;
    html += `<button class="btn-cancel" onclick="window._cc.cancelUploadHandler()">Cancel</button></div>`;
    html += `<div class="progress-bar"><div class="progress-fill" id="uploadProgressFill"></div></div></div>`;
    if (keptClips.length === 0) {
      html += `<div class="empty-state" style="padding:40px 0">No kept clips to upload.</div>`;
    } else {
      html += `<div id="uploadClipList">`;
      for (let i = 0; i < keptClips.length; i++) {
        const clip = keptClips[i];
        const defaultTitle = clip.custom_name || formatClipTitle(clip.filename);
        const alreadyUploaded = !!clip.youtube_url;
        let statusHtml = `<span class="badge badge-ready">Ready</span>`;
        if (clip.youtube_upload_status === 'failed') {
          statusHtml = `<span class="badge badge-error">Failed</span>`;
        } else if (alreadyUploaded) {
          statusHtml = `<a class="upload-link" href="${escapeHtml(clip.youtube_url!)}" target="_blank">Uploaded</a>`;
        }
        html += `<div class="clip-row" id="upload-row-${i}">`;
        html += `<input type="checkbox" class="clip-checkbox upload-cb" data-index="${i}" ${alreadyUploaded ? '' : 'checked'}>`;
        html += `<span class="clip-detail" style="flex-shrink:0;width:120px" title="${escapeHtml(clip.filename)}">${escapeHtml(clip.filename)}</span>`;
        html += `<span class="clip-detail" style="margin-right:4px">Title:</span>`;
        html += `<input type="text" class="title-input upload-title" data-index="${i}" value="${escapeHtml(defaultTitle)}">`;
        html += statusHtml;
        html += `</div>`;
      }
      html += `</div>`;
    }
  }
  html += `</div>`;

  document.getElementById('exportContent')!.innerHTML = html;

  // Post-render: GIF slowdown visibility
  const presetSelect = document.getElementById('encodePreset') as HTMLSelectElement | null;
  if (presetSelect) {
    const updateSlowdown = () => {
      const sg = document.getElementById('slowdownGroup');
      if (sg) sg.style.display = presetSelect.value === 'gif' ? 'flex' : 'none';
    };
    updateSlowdown();
    presetSelect.addEventListener('change', updateSlowdown);
  }

  // Playlist create handler
  const plSelect = document.getElementById('ytPlaylist') as HTMLSelectElement | null;
  if (plSelect) {
    plSelect.addEventListener('change', function () {
      if (this.value === '__create__') { createPlaylistHandler(); this.value = ''; }
    });
  }

  // Initialize compilation UI (from compile.ts)
  window._cc.renderCompilationList();
  window._cc.loadPastCompilations();
}

export function toggleAllClips(section: 'encode' | 'upload'): void {
  const selector = section === 'encode' ? '.encode-cb' : '.upload-cb';
  const checkboxes = Array.from(document.querySelectorAll<HTMLInputElement>(selector));
  if (!checkboxes.length) return;
  const allChecked = checkboxes.every(cb => cb.checked);
  checkboxes.forEach(cb => { cb.checked = !allChecked; });
}

export async function startEncodingHandler(): Promise<void> {
  const checkboxes = Array.from(document.querySelectorAll<HTMLInputElement>('.encode-cb:checked'));
  if (!checkboxes.length) { alert('Select at least one clip to encode.'); return; }

  const preset = (document.getElementById('encodePreset') as HTMLSelectElement).value;
  const fpsVal = (document.getElementById('encodeFps') as HTMLSelectElement).value;
  const fps = fpsVal ? parseInt(fpsVal) : null;
  const slowdownVal = (document.getElementById('encodeSlowdown') as HTMLSelectElement).value;
  const slowdown = slowdownVal ? parseFloat(slowdownVal) : null;

  const clipsToEncode = checkboxes.map(cb => {
    const idx = parseInt(cb.dataset.index!);
    const clip = keptClips[idx];
    return { video_stem: clip.video_stem, filename: clip.filename };
  });

  const btn = document.getElementById('btnEncode') as HTMLButtonElement;
  btn.disabled = true;
  btn.textContent = 'Encoding...';

  try {
    await startEncoding({ clips: clipsToEncode, preset, target_fps: fps, slowdown_factor: slowdown });
    document.getElementById('encodeProgress')!.style.display = 'block';
    encodingPoll = setInterval(pollEncodingStatus, 800);
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false;
    btn.textContent = 'Encode Selected';
  }
}

async function pollEncodingStatus(): Promise<void> {
  try {
    const data = await fetchEncodeStatus();
    const label = document.getElementById('encodeProgressLabel');
    const fill = document.getElementById('encodeProgressFill');
    if (data.total > 0) {
      const pct = Math.round(((data.completed || []).length / data.total) * 100);
      if (label) label.textContent = `Encoding clip ${data.current_index || 0} of ${data.total}: ${data.current_file || ''}`;
      if (fill) fill.style.width = pct + '%';
    }
    if (!data.running) {
      if (encodingPoll) { clearInterval(encodingPoll); encodingPoll = null; }
      const btn = document.getElementById('btnEncode') as HTMLButtonElement;
      btn.disabled = false;
      btn.textContent = 'Encode Selected';
      if (fill) fill.style.width = '100%';
      if (label) {
        label.textContent = data.errors?.length
          ? `Done with ${data.errors.length} error(s). ${(data.completed || []).length} clip(s) encoded.`
          : `Encoding complete! ${(data.completed || []).length} clip(s) encoded.`;
      }
      await loadExportTab();
    }
  } catch (e) { console.error('Encoding poll error:', e); }
}

export async function cancelEncodingHandler(): Promise<void> {
  await cancelEncoding().catch(console.error);
}

export async function startYouTubeAuthHandler(): Promise<void> {
  const clientId = (document.getElementById('ytClientId') as HTMLInputElement).value.trim();
  const clientSecret = (document.getElementById('ytClientSecret') as HTMLInputElement).value.trim();
  if (!clientId || !clientSecret) { alert('Enter both Client ID and Client Secret.'); return; }
  try {
    const data = await startYouTubeAuth(clientId, clientSecret);
    window.open(data.auth_url, 'youtube-auth', 'width=600,height=700');
    const listener = async (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type === 'youtube-auth-success') {
        window.removeEventListener('message', listener);
        await loadExportTab();
      }
    };
    window.addEventListener('message', listener);
  } catch (e) { alert((e as Error).message); }
}

export async function revokeYouTubeAuthHandler(): Promise<void> {
  if (!confirm('Sign out from YouTube?')) return;
  await revokeYouTubeAuth().catch(console.error);
  ytAuthenticated = false;
  ytChannelName = '';
  ytPlaylists = [];
  renderExportView();
}

export async function startUploadHandler(): Promise<void> {
  const checkboxes = Array.from(document.querySelectorAll<HTMLInputElement>('.upload-cb:checked'));
  if (!checkboxes.length) { alert('Select at least one clip to upload.'); return; }

  const privacy = (document.getElementById('ytPrivacy') as HTMLSelectElement).value;
  const playlistId = (document.getElementById('ytPlaylist') as HTMLSelectElement).value;
  const categoryId = (document.getElementById('ytCategory') as HTMLSelectElement).value;
  const tags = (document.getElementById('ytTags') as HTMLInputElement).value.trim();
  const descTemplate = (document.getElementById('ytDescription') as HTMLTextAreaElement).value;

  const clipsToUpload = checkboxes.map(cb => {
    const idx = parseInt(cb.dataset.index!);
    const clip = keptClips[idx];
    const titleInput = document.querySelector<HTMLInputElement>(`.upload-title[data-index="${idx}"]`);
    const title = titleInput?.value.trim() || (clip.custom_name || formatClipTitle(clip.filename));
    let description = descTemplate
      .replace(/\{source_video\}/g, clip.source_video || '')
      .replace(/\{start_time\}/g, clip.start_time ? fmtTime(clip.start_time) : '')
      .replace(/\{end_time\}/g, clip.end_time ? fmtTime(clip.end_time) : '')
      .replace(/\{duration\}/g, clip.duration ? Math.round(clip.duration) + 's' : '')
      .replace(/\{detection_reasons\}/g, (clip.detection_reasons || []).join(', '));
    return {
      video_stem: clip.video_stem, filename: clip.filename,
      use_encoded: !!clip.encoded_exists, title, description,
      tags: tags ? tags.split(',').map(t => t.trim()).filter(t => t) : [],
      category_id: categoryId, privacy, playlist_id: playlistId || null,
    };
  });

  const btn = document.getElementById('btnUpload') as HTMLButtonElement;
  btn.disabled = true; btn.textContent = 'Uploading...';
  try {
    await startUpload({ clips: clipsToUpload });
    document.getElementById('uploadProgress')!.style.display = 'block';
    uploadPoll = setInterval(pollUploadStatus, 1000);
  } catch (e) { alert((e as Error).message); btn.disabled = false; btn.textContent = 'Upload Selected'; }
}

async function pollUploadStatus(): Promise<void> {
  try {
    const data = await fetchUploadStatus();
    const label = document.getElementById('uploadProgressLabel');
    const fill = document.getElementById('uploadProgressFill');
    if (data.total > 0) {
      const filePct = data.bytes_total > 0 ? Math.round((data.bytes_sent / data.bytes_total) * 100) : 0;
      const completedClips = (data.completed || []).length;
      const overallPct = Math.round(((completedClips + filePct / 100) / data.total) * 100);
      if (label) label.textContent = `Uploading clip ${data.current_index || 0} of ${data.total}: ${data.current_file || ''} (${filePct}%)`;
      if (fill) fill.style.width = overallPct + '%';
    }
    if (data.completed) {
      for (const c of data.completed) {
        const idx = keptClips.findIndex(k => k.filename === c.filename);
        if (idx >= 0) {
          const row = document.getElementById('upload-row-' + idx);
          const badge = row?.querySelector('.badge, .upload-link');
          if (badge) badge.outerHTML = `<a class="upload-link" href="${escapeHtml(c.url)}" target="_blank">Uploaded</a>`;
        }
      }
    }
    if (!data.running) {
      if (uploadPoll) { clearInterval(uploadPoll); uploadPoll = null; }
      const btn = document.getElementById('btnUpload') as HTMLButtonElement;
      btn.disabled = false; btn.textContent = 'Upload Selected';
      if (fill) fill.style.width = '100%';
      if (label) {
        label.textContent = data.errors?.length
          ? `Done with ${data.errors.length} error(s). ${(data.completed || []).length} clip(s) uploaded.`
          : `Upload complete! ${(data.completed || []).length} clip(s) uploaded.`;
      }
    }
  } catch (e) { console.error('Upload poll error:', e); }
}

export async function cancelUploadHandler(): Promise<void> {
  await cancelUpload().catch(console.error);
}

async function createPlaylistHandler(): Promise<void> {
  const title = prompt('New playlist title:');
  if (!title?.trim()) return;
  try {
    const newPl = await createPlaylist(title.trim(), 'private');
    ytPlaylists.push(newPl);
    const plSelect = document.getElementById('ytPlaylist') as HTMLSelectElement | null;
    if (plSelect) {
      const createOpt = plSelect.querySelector('option[value="__create__"]');
      const newOpt = document.createElement('option');
      newOpt.value = newPl.id;
      newOpt.textContent = `${newPl.title} (0)`;
      if (createOpt) plSelect.insertBefore(newOpt, createOpt);
      plSelect.value = newPl.id;
    }
  } catch (e) { alert((e as Error).message); }
}
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/static/src/tabs/encode.ts
git commit -m "feat: add tabs/encode.ts module"
```

---

### Task 19: Create src/tabs/compile.ts

**Files:**
- Create: `clipcutter/static/src/tabs/compile.ts`

- [ ] **Step 1: Create compile.ts**

```typescript
import { startCompilation, fetchCompilationStatus, cancelCompilation, fetchCompilations, deleteCompilation } from '../api';
import type { KeptClipInfo } from '../api';
import { escapeHtml } from '../utils';

interface CompilationClip {
  video_stem: string;
  filename: string;
  custom_name: string;
  duration: number;
}

let compilationClips: CompilationClip[] = [];
let compilationPoll: ReturnType<typeof setInterval> | null = null;

export function addSelectedToCompilation(keptClips: KeptClipInfo[]): void {
  const checkboxes = Array.from(document.querySelectorAll<HTMLInputElement>('.encode-cb:checked'));
  if (!checkboxes.length) { alert('Check some clips first.'); return; }
  for (const cb of checkboxes) {
    const idx = parseInt(cb.dataset.index!);
    const clip = keptClips[idx];
    if (!clip) continue;
    if (compilationClips.some(c => c.video_stem === clip.video_stem && c.filename === clip.filename)) continue;
    compilationClips.push({
      video_stem: clip.video_stem,
      filename: clip.filename,
      custom_name: clip.custom_name || clip.filename,
      duration: clip.duration || 0,
    });
  }
  renderCompilationList();
}

export function renderCompilationList(): void {
  const list = document.getElementById('compList');
  if (!list) return;
  if (compilationClips.length === 0) {
    list.innerHTML = '<div style="padding:16px;text-align:center;color:#555;font-size:13px">Add clips using the checkboxes above</div>';
    updateCompDuration();
    return;
  }
  let html = '';
  for (let i = 0; i < compilationClips.length; i++) {
    const c = compilationClips[i];
    const dur = c.duration ? Math.round(c.duration) + 's' : '';
    html += `<div class="comp-item" draggable="true" data-idx="${i}">`;
    html += `<span class="drag-handle">&#x2630;</span>`;
    html += `<span class="comp-clip-name" title="${escapeHtml(c.filename)}">${escapeHtml(c.custom_name)}</span>`;
    html += `<span class="comp-clip-dur">${dur}</span>`;
    html += `<button class="comp-remove" onclick="window._cc.removeCompClip(${i})">&times;</button>`;
    html += `</div>`;
  }
  list.innerHTML = html;
  initCompDragDrop();
  updateCompDuration();
}

export function removeCompClip(idx: number): void {
  compilationClips.splice(idx, 1);
  renderCompilationList();
}

function initCompDragDrop(): void {
  const list = document.getElementById('compList');
  if (!list) return;
  let dragIdx: number | null = null;

  list.querySelectorAll<HTMLElement>('.comp-item').forEach(item => {
    item.addEventListener('dragstart', (e: DragEvent) => {
      dragIdx = parseInt(item.dataset.idx!);
      item.style.opacity = '0.4';
      if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
    });
    item.addEventListener('dragend', () => { item.style.opacity = '1'; });
    item.addEventListener('dragover', (e: DragEvent) => { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'; });
    item.addEventListener('drop', (e: DragEvent) => {
      e.preventDefault();
      const dropIdx = parseInt(item.dataset.idx!);
      if (dragIdx !== null && dragIdx !== dropIdx) {
        const [moved] = compilationClips.splice(dragIdx, 1);
        compilationClips.splice(dropIdx, 0, moved);
        renderCompilationList();
      }
      dragIdx = null;
    });
  });
}

export function updateCompDuration(): void {
  const summary = document.getElementById('compSummary');
  const xfadeGroup = document.getElementById('compXfadeGroup');
  const transition = document.getElementById('compTransition') as HTMLSelectElement | null;

  if (xfadeGroup && transition) {
    xfadeGroup.style.display = transition.value === 'crossfade' ? 'flex' : 'none';
  }

  if (!summary) return;
  if (compilationClips.length === 0) { summary.textContent = 'No clips added yet.'; return; }

  let total = compilationClips.reduce((sum, c) => sum + (c.duration || 0), 0);
  if (transition?.value === 'crossfade' && compilationClips.length > 1) {
    const xfadeDur = parseFloat((document.getElementById('compXfadeDur') as HTMLInputElement)?.value || '0.5');
    total -= (compilationClips.length - 1) * xfadeDur;
  }
  total = Math.max(0, total);
  const m = Math.floor(total / 60);
  const s = Math.round(total % 60);
  summary.textContent = `${compilationClips.length} clips \u2014 Total: ${m}:${String(s).padStart(2, '0')}`;
}

export async function startCompilationHandler(): Promise<void> {
  if (compilationClips.length < 2) { alert('Add at least 2 clips.'); return; }

  const transition = (document.getElementById('compTransition') as HTMLSelectElement)?.value || 'cut';
  const xfadeDur = parseFloat((document.getElementById('compXfadeDur') as HTMLInputElement)?.value || '0.5');
  const title = (document.getElementById('compTitle') as HTMLInputElement)?.value?.trim() || '';

  const btn = document.getElementById('btnBuildComp') as HTMLButtonElement;
  btn.disabled = true; btn.textContent = 'Building...';

  try {
    await startCompilation({
      clips: compilationClips.map(c => ({ video_stem: c.video_stem, filename: c.filename })),
      transition, crossfade_duration: xfadeDur, title,
    });
    document.getElementById('compProgress')!.style.display = 'block';
    compilationPoll = setInterval(pollCompilationStatus, 1000);
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false; btn.textContent = 'Build';
  }
}

async function pollCompilationStatus(): Promise<void> {
  try {
    const data = await fetchCompilationStatus();
    const label = document.getElementById('compProgressLabel');
    const fill = document.getElementById('compProgressFill');
    if (label) label.textContent = data.current_step || 'Building...';
    if (fill) fill.style.width = data.progress_pct + '%';

    if (!data.running) {
      if (compilationPoll) { clearInterval(compilationPoll); compilationPoll = null; }
      const btn = document.getElementById('btnBuildComp') as HTMLButtonElement | null;
      if (btn) { btn.disabled = false; btn.textContent = 'Build'; }
      if (fill) fill.style.width = '100%';
      if (data.error) {
        if (label) label.textContent = 'Error: ' + data.error;
      } else {
        if (label) label.textContent = 'Compilation complete! ' + (data.output_filename || '');
        compilationClips = [];
        renderCompilationList();
      }
      loadPastCompilations();
    }
  } catch (e) { console.error('Compilation poll error:', e); }
}

export async function cancelCompilationHandler(): Promise<void> {
  await cancelCompilation().catch(() => {});
}

export async function loadPastCompilations(): Promise<void> {
  const container = document.getElementById('pastCompilations');
  if (!container) return;
  try {
    const data = await fetchCompilations();
    const comps = (data.compilations || []).filter(c => c.file_exists);
    if (comps.length === 0) { container.innerHTML = ''; return; }
    let html = '<h3 style="font-size:14px;color:#fff;margin:16px 0 8px">Past Compilations</h3>';
    for (const comp of comps) {
      const dur = comp.total_duration ? Math.round(comp.total_duration) + 's' : '';
      html += `<div class="comp-past-item">`;
      html += `<span class="clip-name" style="flex:1">${escapeHtml(comp.filename)}</span>`;
      html += `<span class="clip-detail">${comp.clip_count} clips</span>`;
      html += `<span class="clip-detail">${dur}</span>`;
      html += `<a href="/video/compilation/${encodeURIComponent(comp.filename)}" target="_blank" class="upload-link">Play</a>`;
      html += `<button class="comp-remove" onclick="window._cc.deleteCompilationHandler('${escapeHtml(comp.compilation_id)}')">&times;</button>`;
      html += `</div>`;
    }
    container.innerHTML = html;
  } catch (e) { console.error('Failed to load compilations:', e); }
}

export async function deleteCompilationHandler(compId: string): Promise<void> {
  if (!confirm('Delete this compilation?')) return;
  try {
    await deleteCompilation(compId);
    loadPastCompilations();
  } catch (e) { alert((e as Error).message); }
}
```

- [ ] **Step 2: Commit**

```bash
git add clipcutter/static/src/tabs/compile.ts
git commit -m "feat: add tabs/compile.ts module"
```

---

### Task 20: Create src/main.ts and rewrite index.html as Vite entry

**Files:**
- Create: `clipcutter/static/src/main.ts`
- Modify: `clipcutter/static/index.html` (replace with Vite entry point shell)

- [ ] **Step 1: Create main.ts**

```typescript
import { initProcessTab, startProcessingHandler } from './tabs/process';
import { loadClips, clipAction, setTrimPoint, seekTo, updateTrimIndicator, stopWaveformSync, deleteSourceHandler } from './tabs/review';
import { loadExportTab, renderExportView, toggleAllClips, startEncodingHandler, cancelEncodingHandler, startYouTubeAuthHandler, revokeYouTubeAuthHandler, startUploadHandler, cancelUploadHandler, keptClips } from './tabs/encode';
import { addSelectedToCompilation, renderCompilationList, removeCompClip, updateCompDuration, startCompilationHandler, cancelCompilationHandler, loadPastCompilations, deleteCompilationHandler } from './tabs/compile';

// Expose handlers to HTML via window._cc (avoids global namespace pollution)
declare global {
  interface Window {
    _cc: typeof handlers;
    _savedVol: number;
  }
}

const handlers = {
  // Process
  startProcessingHandler,
  // Review
  clipAction,
  setTrimPoint,
  seekTo,
  updateTrimIndicator,
  deleteSourceHandler,
  // Encode
  toggleAllClips,
  startEncodingHandler,
  cancelEncodingHandler,
  startYouTubeAuthHandler,
  revokeYouTubeAuthHandler,
  startUploadHandler,
  cancelUploadHandler,
  addSelectedToCompilation: () => addSelectedToCompilation(keptClips),
  // Compile
  renderCompilationList,
  removeCompClip,
  updateCompDuration,
  startCompilationHandler,
  cancelCompilationHandler,
  loadPastCompilations,
  deleteCompilationHandler,
};

window._cc = handlers;
window._savedVol = 0.5;

let activeTab = 'process';

function switchTab(tab: string): void {
  activeTab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelector(`.tab[data-tab="${tab}"]`)?.classList.add('active');
  document.getElementById('view-' + tab)?.classList.add('active');

  if (tab !== 'review') stopWaveformSync();
  if (tab === 'review') loadClips();
  if (tab === 'export') loadExportTab();
}

// Tab click handlers exposed to HTML
(document.querySelectorAll('.tab') as NodeListOf<HTMLElement>).forEach(el => {
  el.addEventListener('click', () => switchTab(el.dataset.tab!));
});

// Keyboard shortcuts (review tab only)
document.addEventListener('keydown', (e: KeyboardEvent) => {
  if (activeTab !== 'review') return;
  const target = e.target as HTMLElement;
  if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return;

  switch (e.key.toLowerCase()) {
    case 'k': e.preventDefault(); clipAction('keep'); break;
    case 'd': e.preventDefault(); clipAction('discard'); break;
    case 's': e.preventDefault(); clipAction('skip'); break;
    case 'i': e.preventDefault(); setTrimPoint('in'); break;
    case 'o': e.preventDefault(); setTrimPoint('out'); break;
    case 'n': e.preventDefault(); (document.getElementById('clipCustomName') as HTMLInputElement | null)?.focus(); break;
    case ' ':
      e.preventDefault();
      const player = document.getElementById('player') as HTMLVideoElement | null;
      if (player) player.paused ? player.play() : player.pause();
      break;
  }
});

// Trim indicator on input change
document.addEventListener('input', (e: Event) => {
  const target = e.target as HTMLElement;
  if (target.id === 'trimIn' || target.id === 'trimOut') updateTrimIndicator();
});

// App init
initProcessTab();
```

- [ ] **Step 2: Replace index.html with Vite entry point**

The new `clipcutter/static/index.html` is a Vite entry that references `src/main.ts`. Copy the `<style>` block (lines 1–648 of the original) and the DOM skeleton (lines 650–698) verbatim. Remove the entire `<script>` block (lines 699–2148). Add the Vite script tag.

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ClipCutter</title>
  <!-- Copy the full <style>...</style> block verbatim from clipcutter/static/index.html lines 3–648.
       This is ~645 lines of existing CSS (dark theme, all component classes). Do not change any class names. -->
</head>
<body>

<div id="overlay" class="overlay">
  <div id="overlayText" class="overlay-text"></div>
</div>

<div class="header">
  <h1>ClipCutter</h1>
  <div class="tabs">
    <div class="tab active" data-tab="process">Process</div>
    <div class="tab" data-tab="review">Review</div>
    <div class="tab" data-tab="export">Export</div>
  </div>
  <div class="header-right" id="headerRight"></div>
</div>

<div class="main">
  <div class="view active" id="view-process">
    <div class="form-section">
      <h2>Process Videos</h2>
      <div class="form-row">
        <div class="form-group wide">
          <label>Video folder</label>
          <input type="text" id="folderPath" placeholder="C:\path\to\videos" />
        </div>
        <div class="form-group">
          <label>Sensitivity</label>
          <input type="number" id="sensitivity" value="1.0" min="0.1" max="5.0" step="0.1" />
        </div>
        <div class="form-group">
          <label>Context (s)</label>
          <input type="number" id="context" placeholder="20" min="0" step="1" />
        </div>
        <button class="btn-process" id="btnProcess" onclick="window._cc.startProcessingHandler()">Process</button>
      </div>
    </div>
    <div class="log-box" id="logBox"></div>
  </div>

  <div class="view" id="view-review">
    <div id="reviewContent">
      <div class="empty-state">No pending clips. Process some videos first.</div>
    </div>
  </div>

  <div class="view" id="view-export">
    <div id="exportContent">
      <div class="empty-state">Loading export data...</div>
    </div>
  </div>
</div>

<script type="module" src="/src/main.ts"></script>
</body>
</html>
```

- [ ] **Step 3: Commit**

```bash
git add clipcutter/static/src/main.ts clipcutter/static/index.html
git commit -m "feat: add main.ts entry point and rewrite index.html as Vite entry"
```

---

### Task 21: Update web.py to serve dist/ and verify full build

**Files:**
- Modify: `clipcutter/web.py` (index route)

- [ ] **Step 1: Build the frontend**

```bash
cd E:/Projects/ClipCutter/clipcutter/static && npm run build
```
Expected: `dist/index.html` and `dist/assets/` created. Fix any TypeScript errors before continuing.

- [ ] **Step 2: Update web.py to serve dist/index.html**

Change the `index()` route in `clipcutter/web.py`:
```python
    @app.get("/", response_class=HTMLResponse)
    def index():
        dist_path = STATIC_DIR / "dist" / "index.html"
        if dist_path.exists():
            return dist_path.read_text(encoding="utf-8")
        # Fallback for dev: serve old index.html if dist not built yet
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")
```

Also mount the `dist/assets/` directory for static assets:
```python
from fastapi.staticfiles import StaticFiles

# In create_app(), after registering all routers:
dist_dir = STATIC_DIR / "dist"
if dist_dir.exists():
    app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")
```

- [ ] **Step 3: Run all tests**

```bash
cd E:/Projects/ClipCutter && pytest tests/ -v -k "not browser"
```
Expected: all API tests pass.

- [ ] **Step 4: Commit**

```bash
git add clipcutter/web.py
git commit -m "feat: serve Vite dist/ build from FastAPI"
```

---

## Phase 3: New Features

### Task 22: Add DELETE /api/compilation/{id}/sources endpoint

**Files:**
- Modify: `clipcutter/routes/compile.py`
- Modify: `clipcutter/static/src/api.ts`

- [ ] **Step 1: Add endpoint to compile router**

Add the following route inside `create_router()` in `clipcutter/routes/compile.py`, after the `delete_compilation` route:

```python
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
```

- [ ] **Step 2: Add API function to api.ts**

Add to `clipcutter/static/src/api.ts`:
```typescript
export const deleteCompilationSources = (compId: string) =>
  apiDelete(`/api/compilation/${compId}/sources`);
```

Change the `apiDelete` function to return the response body:
```typescript
async function apiDelete<T = void>(url: string): Promise<T> {
  const res = await fetch(url, { method: 'DELETE' });
  if (!res.ok) throw new Error(res.statusText);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
```

- [ ] **Step 3: Commit**

```bash
git add clipcutter/routes/compile.py clipcutter/static/src/api.ts
git commit -m "feat: add endpoint to delete source clips after compilation"
```

---

### Task 23: Add "Clean up source clips" button to compile.ts

**Files:**
- Modify: `clipcutter/static/src/tabs/compile.ts`

- [ ] **Step 1: Import deleteCompilationSources and add handler**

Add to the imports at the top of `compile.ts`:
```typescript
import { deleteCompilationSources } from '../api';
```

Add the handler function before `loadPastCompilations`:
```typescript
export async function deleteCompilationSourcesHandler(compId: string, clipCount: number): Promise<void> {
  if (!confirm(`Delete the ${clipCount} source clip(s) used in this compilation? This cannot be undone.`)) return;
  try {
    const data = await deleteCompilationSources(compId) as { deleted_count: number };
    alert(`Deleted ${data.deleted_count} clip file(s).`);
    loadPastCompilations();
  } catch (e) {
    alert((e as Error).message);
  }
}
```

- [ ] **Step 2: Add button to past compilations HTML**

In `loadPastCompilations()`, change the compilation card HTML. After the delete button, add:
```typescript
      html += `<button class="btn-secondary" style="font-size:12px;padding:4px 8px" onclick="window._cc.deleteCompilationSourcesHandler('${escapeHtml(comp.compilation_id)}', ${comp.clip_count})">Clean up clips</button>`;
```

So the full card becomes:
```typescript
    for (const comp of comps) {
      const dur = comp.total_duration ? Math.round(comp.total_duration) + 's' : '';
      html += `<div class="comp-past-item">`;
      html += `<span class="clip-name" style="flex:1">${escapeHtml(comp.filename)}</span>`;
      html += `<span class="clip-detail">${comp.clip_count} clips</span>`;
      html += `<span class="clip-detail">${dur}</span>`;
      html += `<a href="/video/compilation/${encodeURIComponent(comp.filename)}" target="_blank" class="upload-link">Play</a>`;
      html += `<button class="btn-secondary" style="font-size:12px;padding:4px 8px" onclick="window._cc.deleteCompilationSourcesHandler('${escapeHtml(comp.compilation_id)}', ${comp.clip_count})">Clean up clips</button>`;
      html += `<button class="comp-remove" onclick="window._cc.deleteCompilationHandler('${escapeHtml(comp.compilation_id)}')">&times;</button>`;
      html += `</div>`;
    }
```

- [ ] **Step 3: Expose in main.ts**

In `clipcutter/static/src/main.ts`, add to the imports and handlers object:
```typescript
import { ..., deleteCompilationSourcesHandler } from './tabs/compile';
// In handlers:
deleteCompilationSourcesHandler,
```

- [ ] **Step 4: Build and test**

```bash
cd E:/Projects/ClipCutter/clipcutter/static && npm run build
cd E:/Projects/ClipCutter && pytest tests/ -v -k "not browser"
```
Expected: build succeeds, all API tests pass.

- [ ] **Step 5: Commit**

```bash
git add clipcutter/static/src/tabs/compile.ts clipcutter/static/src/main.ts clipcutter/static/dist
git commit -m "feat: add clean-up source clips button after compilation"
```

---

### Task 24: Update KeepRequest for multi-segment support (backend)

**Files:**
- Modify: `clipcutter/routes/review.py`

- [ ] **Step 1: Update the KeepRequest model and keep_clip handler**

Replace the `KeepRequest` class and `keep_clip` function in `clipcutter/routes/review.py`:

```python
class Segment(BaseModel):
    start: float
    end: float


class KeepRequest(BaseModel):
    segments: list[Segment]
    custom_name: Optional[str] = None
```

Replace the `keep_clip` endpoint:

```python
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

        if req and req.custom_name and req.custom_name.strip():
            update_clip_custom_name(meta_path, filename, req.custom_name.strip())

        return {"status": "kept", "trimmed": trimmed}
```

- [ ] **Step 2: Run API tests**

```bash
cd E:/Projects/ClipCutter && pytest tests/ -v -k "not browser"
```
Expected: all pass. The new `KeepRequest` schema replaces the old one; tests that call `/keep` need to send `segments` instead of `trim_start/trim_end`. Update any such tests now.

Existing keep test likely sends `{}` (no body, defaults). Since `segments` has no default, it will 422. Update the test to send `{"segments": []}` (empty = full clip):

Find the keep test in `tests/` and update the POST body:
```python
# Old:
client.post(f"/api/clips/{stem}/{filename}/keep", json={})
# New:
client.post(f"/api/clips/{stem}/{filename}/keep", json={"segments": []})
```

- [ ] **Step 3: Commit**

```bash
git add clipcutter/routes/review.py tests/
git commit -m "feat: update KeepRequest to support multiple cut segments"
```

---

### Task 25: Update review.ts for multi-segment UI

**Files:**
- Modify: `clipcutter/static/src/api.ts`
- Modify: `clipcutter/static/src/tabs/review.ts`

- [ ] **Step 1: Update KeepRequest type in api.ts**

Replace the `KeepRequest` interface in `api.ts`:
```typescript
export interface Segment {
  start: number;
  end: number;
}

export interface KeepRequest {
  segments: Segment[];
  custom_name: string | null;
}
```

Update the `keepClip` wrapper to match:
```typescript
export const keepClip = (stem: string, filename: string, req: KeepRequest) =>
  apiPost<{ status: string; trimmed: boolean }>(`/api/clips/${stem}/${filename}/keep`, req);
```

- [ ] **Step 2: Update the review.ts trim section to support multiple segments**

Replace the `segments` state variable and trim section rendering in `review.ts`. Add segment state at the top:

```typescript
interface SegmentEntry {
  start: number;
  end: number;
}

let segments: SegmentEntry[] = [];
let activeSegmentIndex = 0;
```

Replace the trim section HTML inside `showClip()`:

```typescript
  // After the clip-info div, replace the single trim-section with:
  const segmentsHtml = `
    <div class="trim-section" id="segmentList">
      <div class="segment-row" data-seg="0">
        <span class="trim-label">Seg 1</span>
        <span class="trim-label" style="margin-left:8px">In</span>
        <input type="text" class="trim-time seg-in" data-seg="0" value="0:00" />
        <button class="trim-btn" onclick="window._cc.setSegmentPoint(0,'in')">Set</button>
        <button class="trim-btn" onclick="window._cc.seekToSegment(0,'in')">Go</button>
        <span class="trim-label" style="margin-left:8px">Out</span>
        <input type="text" class="trim-time seg-out" data-seg="0" value="${fmtTimePrecise(clip.duration)}" />
        <button class="trim-btn" onclick="window._cc.setSegmentPoint(0,'out')">Set</button>
        <button class="trim-btn" onclick="window._cc.seekToSegment(0,'out')">Go</button>
      </div>
    </div>
    <div style="margin:6px 0">
      <button class="trim-btn" onclick="window._cc.addSegment()">+ Add segment</button>
      <span class="trim-indicator" id="trimIndicator"></span>
    </div>
  `;
```

Initialize segments when showing a clip (replace the old trimIn/trimOut state):
```typescript
  // After building the HTML, reset segment state:
  segments = [{ start: 0, end: clip.duration }];
  activeSegmentIndex = 0;
```

Add the segment manipulation functions:

```typescript
export function addSegment(): void {
  const clip = clips[currentIndex];
  if (!clip) return;
  const lastEnd = segments[segments.length - 1]?.end ?? clip.duration;
  const newStart = Math.min(lastEnd, clip.duration - 10);
  segments.push({ start: Math.max(0, newStart), end: clip.duration });
  renderSegments(clip.duration);
}

export function removeSegment(idx: number): void {
  if (segments.length <= 1) return; // must keep at least one
  segments.splice(idx, 1);
  const clip = clips[currentIndex];
  if (clip) renderSegments(clip.duration);
}

function renderSegments(clipDuration: number): void {
  const list = document.getElementById('segmentList');
  if (!list) return;
  let html = '';
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    html += `<div class="segment-row" data-seg="${i}" onclick="window._cc.focusSegment(${i})" style="cursor:pointer;${i === activeSegmentIndex ? 'outline:1px solid #60a5fa;' : ''}">`;
    html += `<span class="trim-label">Seg ${i + 1}</span>`;
    html += `<span class="trim-label" style="margin-left:8px">In</span>`;
    html += `<input type="text" class="trim-time seg-in" data-seg="${i}" value="${fmtTimePrecise(seg.start)}" oninput="window._cc.onSegmentInput(${i},'in',this.value)" />`;
    html += `<button class="trim-btn" onclick="window._cc.setSegmentPoint(${i},'in')">Set</button>`;
    html += `<button class="trim-btn" onclick="window._cc.seekToSegment(${i},'in')">Go</button>`;
    html += `<span class="trim-label" style="margin-left:8px">Out</span>`;
    html += `<input type="text" class="trim-time seg-out" data-seg="${i}" value="${fmtTimePrecise(seg.end)}" oninput="window._cc.onSegmentInput(${i},'out',this.value)" />`;
    html += `<button class="trim-btn" onclick="window._cc.setSegmentPoint(${i},'out')">Set</button>`;
    html += `<button class="trim-btn" onclick="window._cc.seekToSegment(${i},'out')">Go</button>`;
    if (segments.length > 1) {
      html += `<button class="comp-remove" onclick="window._cc.removeSegment(${i})">&times;</button>`;
    }
    html += `</div>`;
  }
  list.innerHTML = html;
  updateTrimIndicator();
}

export function focusSegment(idx: number): void {
  activeSegmentIndex = idx;
  const clip = clips[currentIndex];
  if (clip) renderSegments(clip.duration);
}

export function setSegmentPoint(segIdx: number, which: 'in' | 'out'): void {
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (!player) return;
  const time = player.currentTime;
  if (which === 'in') segments[segIdx].start = time;
  else segments[segIdx].end = time;
  const clip = clips[currentIndex];
  if (clip) renderSegments(clip.duration);
}

export function seekToSegment(segIdx: number, which: 'in' | 'out'): void {
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (!player) return;
  player.currentTime = which === 'in' ? segments[segIdx].start : segments[segIdx].end;
}

export function onSegmentInput(segIdx: number, which: 'in' | 'out', val: string): void {
  const t = parseTrimTime(val);
  if (which === 'in') segments[segIdx].start = t;
  else segments[segIdx].end = t;
  updateTrimIndicator();
}
```

Update `updateTrimIndicator()` to use the segments array:
```typescript
export function updateTrimIndicator(): void {
  const clip = clips[currentIndex];
  if (!clip) return;
  const indicator = document.getElementById('trimIndicator')!;
  const isFullClip = segments.length === 1 && segments[0].start <= 0.1 && (clip.duration - segments[0].end) <= 0.1;
  if (!isFullClip) {
    const totalKept = segments.reduce((sum, s) => sum + Math.max(0, s.end - s.start), 0);
    indicator.textContent = segments.length > 1
      ? `${segments.length} segments \u2014 ${totalKept.toFixed(1)}s kept`
      : `Trimmed: ${totalKept.toFixed(1)}s`;
    indicator.className = 'trim-indicator active';
  } else {
    indicator.textContent = '';
    indicator.className = 'trim-indicator';
  }
}
```

Update `clipAction()` to send segments instead of trim_start/trim_end:
```typescript
  if (type === 'keep') {
    const customName = (document.getElementById('clipCustomName') as HTMLInputElement)?.value?.trim() || null;
    const isFullClip = segments.length === 1 && segments[0].start <= 0.1 && (clip.duration - segments[0].end) <= 0.1;
    showOverlay(isFullClip ? 'Saving clip...' : segments.length > 1 ? 'Cutting segments...' : 'Trimming clip...');
    try {
      await keepClip(clip.video_stem, clip.filename, {
        segments: segments.map(s => ({ start: s.start, end: s.end })),
        custom_name: customName,
      });
    } catch (e) {
      hideOverlay();
      alert((e as Error).message || 'Keep failed');
      return;
    }
    hideOverlay();
    results[currentIndex] = 'kept';
  }
```

Update keyboard shortcuts I/O to use `setSegmentPoint(activeSegmentIndex, ...)`:
```typescript
    case 'i': e.preventDefault(); setSegmentPoint(activeSegmentIndex, 'in'); break;
    case 'o': e.preventDefault(); setSegmentPoint(activeSegmentIndex, 'out'); break;
```

Export the new functions and update `main.ts` handlers:
```typescript
// In main.ts imports from review.ts, add:
import { ..., addSegment, removeSegment, focusSegment, setSegmentPoint, seekToSegment, onSegmentInput } from './tabs/review';

// In handlers:
addSegment,
removeSegment,
focusSegment,
setSegmentPoint,
seekToSegment,
onSegmentInput,
```

- [ ] **Step 3: Build and run full tests**

```bash
cd E:/Projects/ClipCutter/clipcutter/static && npm run build
cd E:/Projects/ClipCutter && pytest tests/ -v
```
Expected: all tests pass, build succeeds.

- [ ] **Step 4: Commit**

```bash
git add clipcutter/static/src/api.ts clipcutter/static/src/tabs/review.ts clipcutter/static/src/main.ts clipcutter/static/dist
git commit -m "feat: multi-segment cut UI in review tab"
```

---

## Final Verification

- [ ] Run the full test suite one last time:

```bash
cd E:/Projects/ClipCutter && pytest tests/ -v
```
Expected: all 34+ tests pass.

- [ ] Start the app manually and smoke-test all tabs:

```bash
python -m clipcutter ui
```

Verify:
1. Process tab: folder input pre-filled, processing starts
2. Review tab: clips load, pause on keep/discard works, trim works without re-encoding on untrimmed, multi-segment UI appears
3. Export tab: custom names show in encode section, compilation cards show "Clean up clips" button
