"""Web UI for clip processing and review."""

import shutil
import sys
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from clipcutter.config import DIR_CLIPS, DIR_DISCARDED, DIR_KEPT, DIR_METADATA, DIR_PENDING
from clipcutter.metadata import load_metadata, load_metadata_dict, update_clip_status


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
        return PAGE_HTML

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
                from clipcutter.pipeline import process_video, process_directory

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

        return {"status": "discarded"}

    return app


# ======================================================================
# HTML / CSS / JS  (single-page app with Process and Review tabs)
# ======================================================================

PAGE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ClipCutter</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: #0f0f0f;
    color: #e0e0e0;
    min-height: 100vh;
  }

  /* ---- Header / Tabs ---- */
  .header {
    display: flex;
    align-items: center;
    padding: 0 24px;
    background: #1a1a1a;
    border-bottom: 1px solid #2a2a2a;
    gap: 24px;
  }
  .header h1 { font-size: 18px; font-weight: 600; color: #fff; padding: 14px 0; }
  .tabs { display: flex; gap: 4px; }
  .tab {
    padding: 14px 18px;
    font-size: 14px;
    font-weight: 500;
    color: #888;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    transition: color 0.15s, border-color 0.15s;
  }
  .tab:hover { color: #ccc; }
  .tab.active { color: #fff; border-bottom-color: #60a5fa; }
  .header-right { margin-left: auto; font-size: 13px; color: #666; }

  .main { max-width: 960px; margin: 0 auto; padding: 24px; }
  .view { display: none; }
  .view.active { display: block; }

  /* ---- Process view ---- */
  .form-section {
    background: #1a1a1a;
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 20px;
  }
  .form-section h2 { font-size: 16px; font-weight: 600; color: #fff; margin-bottom: 16px; }
  .form-row {
    display: flex;
    gap: 12px;
    margin-bottom: 12px;
    align-items: end;
  }
  .form-group { display: flex; flex-direction: column; gap: 4px; }
  .form-group.wide { flex: 1; }
  .form-group label { font-size: 12px; color: #888; font-weight: 500; }
  .form-group input[type="text"],
  .form-group input[type="number"] {
    padding: 10px 12px;
    background: #0f0f0f;
    border: 1px solid #333;
    border-radius: 8px;
    color: #fff;
    font-size: 14px;
    font-family: inherit;
    outline: none;
    transition: border-color 0.15s;
  }
  .form-group input:focus { border-color: #60a5fa; }
  .form-group input[type="number"] { width: 100px; }

  .btn-process {
    padding: 10px 28px;
    background: #2563eb;
    color: #fff;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s;
    white-space: nowrap;
  }
  .btn-process:hover { background: #1d4ed8; }
  .btn-process:disabled { background: #333; color: #666; cursor: not-allowed; }

  .log-box {
    background: #0a0a0a;
    border: 1px solid #222;
    border-radius: 8px;
    padding: 16px;
    font-family: 'Consolas', 'SF Mono', monospace;
    font-size: 13px;
    line-height: 1.6;
    color: #aaa;
    max-height: 400px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .log-box .log-line { margin-bottom: 1px; }
  .log-box .log-done { color: #4ade80; font-weight: 600; }
  .log-box .log-error { color: #f87171; font-weight: 600; }
  .log-box:empty::after { content: 'Waiting to start...'; color: #444; }

  /* ---- Review view ---- */
  .empty-state {
    text-align: center;
    padding: 80px 24px;
    color: #666;
    font-size: 16px;
  }
  .player-section {
    background: #1a1a1a;
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 20px;
  }
  video {
    width: 100%;
    display: block;
    background: #000;
    max-height: 480px;
  }
  .clip-info { padding: 20px 24px; }
  .clip-title { font-size: 16px; font-weight: 600; color: #fff; margin-bottom: 8px; }
  .clip-meta {
    display: flex; gap: 20px; flex-wrap: wrap;
    font-size: 13px; color: #999; margin-bottom: 12px;
  }
  .clip-meta span { display: flex; align-items: center; gap: 4px; }
  .tags { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; }
  .tag {
    display: inline-block; padding: 3px 10px; border-radius: 12px;
    font-size: 12px; font-weight: 500;
  }
  .tag-volume_spike { background: #3b1f1f; color: #f87171; }
  .tag-laughter { background: #1f3b2a; color: #4ade80; }
  .tag-shouting { background: #3b2f1f; color: #fbbf24; }
  .tag-sudden_noise { background: #1f2a3b; color: #60a5fa; }
  .tag-fallback { background: #2a2a2a; color: #888; }
  .confidence-bar { height: 4px; background: #2a2a2a; border-radius: 2px; overflow: hidden; }
  .confidence-fill { height: 100%; border-radius: 2px; transition: width 0.3s; }

  .actions { display: flex; gap: 12px; padding: 0 24px 24px; }
  .btn {
    flex: 1; padding: 14px; border: none; border-radius: 10px;
    font-size: 15px; font-weight: 600; cursor: pointer;
    transition: transform 0.1s, opacity 0.15s;
    display: flex; flex-direction: column; align-items: center; gap: 2px;
  }
  .btn:hover { opacity: 0.9; }
  .btn:active { transform: scale(0.97); }
  .btn .shortcut { font-size: 11px; font-weight: 400; opacity: 0.6; }
  .btn-keep { background: #166534; color: #fff; }
  .btn-skip { background: #333; color: #ccc; }
  .btn-discard { background: #7f1d1d; color: #fff; }

  .progress { display: flex; gap: 4px; margin-bottom: 20px; height: 3px; }
  .progress-dot { flex: 1; border-radius: 2px; background: #2a2a2a; transition: background 0.3s; }
  .progress-dot.done { background: #166534; }
  .progress-dot.discarded { background: #7f1d1d; }
  .progress-dot.current { background: #60a5fa; }

  .done-state { text-align: center; padding: 80px 24px; }
  .done-state h2 { font-size: 24px; color: #fff; margin-bottom: 12px; }
  .done-state p { color: #888; font-size: 15px; margin-bottom: 4px; }

  .keyboard-hint { text-align: center; font-size: 12px; color: #555; padding-top: 8px; }
  kbd {
    display: inline-block; padding: 2px 6px; background: #222;
    border: 1px solid #333; border-radius: 4px;
    font-family: inherit; font-size: 11px; color: #aaa;
  }
</style>
</head>
<body>

<div class="header">
  <h1>ClipCutter</h1>
  <div class="tabs">
    <div class="tab active" onclick="switchTab('process')">Process</div>
    <div class="tab" onclick="switchTab('review')">Review</div>
  </div>
  <div class="header-right" id="headerRight"></div>
</div>

<div class="main">
  <!-- ============ PROCESS VIEW ============ -->
  <div class="view active" id="view-process">
    <div class="form-section">
      <h2>Process Videos</h2>
      <div class="form-row">
        <div class="form-group wide">
          <label>Video folder</label>
          <input type="text" id="folderPath" placeholder="C:\\path\\to\\videos" />
        </div>
        <div class="form-group">
          <label>Sensitivity</label>
          <input type="number" id="sensitivity" value="1.0" min="0.1" max="5.0" step="0.1" />
        </div>
        <div class="form-group">
          <label>Context (s)</label>
          <input type="number" id="context" placeholder="20" min="0" step="1" />
        </div>
        <button class="btn-process" id="btnProcess" onclick="startProcessing()">Process</button>
      </div>
    </div>
    <div class="log-box" id="logBox"></div>
  </div>

  <!-- ============ REVIEW VIEW ============ -->
  <div class="view" id="view-review">
    <div id="reviewContent">
      <div class="empty-state">No pending clips. Process some videos first.</div>
    </div>
  </div>
</div>

<script>
// ---- Tab switching ----
let activeTab = 'process';

function switchTab(tab) {
  activeTab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelector(`.tab:nth-child(${tab === 'process' ? 1 : 2})`).classList.add('active');
  document.getElementById('view-' + tab).classList.add('active');

  if (tab === 'review') loadClips();
}

// ---- Processing ----
let pollTimer = null;

async function startProcessing() {
  const folder = document.getElementById('folderPath').value.trim();
  if (!folder) { alert('Enter a folder path.'); return; }

  const sensitivity = parseFloat(document.getElementById('sensitivity').value) || 1.0;
  const ctxVal = document.getElementById('context').value.trim();
  const context = ctxVal ? parseFloat(ctxVal) : null;

  const btn = document.getElementById('btnProcess');
  btn.disabled = true;
  btn.textContent = 'Processing...';
  document.getElementById('logBox').innerHTML = '';

  try {
    const res = await fetch('/api/process', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({folder, sensitivity, context}),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(err.detail || 'Failed to start processing');
      btn.disabled = false;
      btn.textContent = 'Process';
      return;
    }
    pollTimer = setInterval(pollStatus, 800);
  } catch (e) {
    alert('Error: ' + e.message);
    btn.disabled = false;
    btn.textContent = 'Process';
  }
}

async function pollStatus() {
  const res = await fetch('/api/process/status');
  const data = await res.json();
  const box = document.getElementById('logBox');

  box.innerHTML = data.log.map(line =>
    `<div class="log-line">${escapeHtml(line)}</div>`
  ).join('');
  box.scrollTop = box.scrollHeight;

  if (!data.running) {
    clearInterval(pollTimer);
    pollTimer = null;
    const btn = document.getElementById('btnProcess');
    btn.disabled = false;
    btn.textContent = 'Process';

    if (data.error) {
      box.innerHTML += `<div class="log-line log-error">Error: ${escapeHtml(data.error)}</div>`;
    } else {
      box.innerHTML += `<div class="log-line log-done">Done! Switch to Review tab to review clips.</div>`;
    }
    box.scrollTop = box.scrollHeight;
  }
}

function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}

// ---- Review ----
let clips = [];
let currentIndex = 0;
let results = [];
let savedVolume = 0.5;

async function loadClips() {
  const res = await fetch('/api/clips');
  const data = await res.json();
  clips = data.clips;
  results = new Array(clips.length).fill(null);
  currentIndex = 0;

  if (clips.length === 0) {
    showEmpty();
  } else {
    showClip();
  }
}

function showEmpty() {
  document.getElementById('headerRight').textContent = '';
  document.getElementById('reviewContent').innerHTML =
    '<div class="empty-state">No pending clips. Process some videos first.</div>';
}

function showClip() {
  if (currentIndex >= clips.length) { showDone(); return; }

  const clip = clips[currentIndex];
  const remaining = clips.length - currentIndex;
  document.getElementById('headerRight').textContent =
    `Clip ${currentIndex + 1} of ${clips.length}`;

  const confPercent = Math.round(clip.confidence * 100);
  const confColor = clip.confidence > 0.7 ? '#4ade80' :
                    clip.confidence > 0.4 ? '#fbbf24' : '#f87171';
  const tags = clip.detection_reasons.map(r =>
    `<span class="tag tag-${r}">${r.replace('_', ' ')}</span>`
  ).join('');
  const startFmt = fmtTime(clip.start_time);
  const endFmt = fmtTime(clip.end_time);
  const durFmt = Math.round(clip.duration);

  document.getElementById('reviewContent').innerHTML = `
    <div class="progress">
      ${clips.map((_, i) => {
        let cls = 'progress-dot';
        if (i < currentIndex) cls += results[i] === 'discarded' ? ' discarded' : ' done';
        else if (i === currentIndex) cls += ' current';
        return `<div class="${cls}"></div>`;
      }).join('')}
    </div>
    <div class="player-section">
      <video id="player" controls autoplay onvolumechange="savedVolume = this.volume"
             onloadeddata="this.volume = savedVolume">
        <source src="${clip.video_url}" type="video/mp4">
      </video>
      <div class="clip-info">
        <div class="clip-title">${clip.source_video}</div>
        <div class="clip-meta">
          <span>${startFmt} - ${endFmt}</span>
          <span>${durFmt}s</span>
          <span>Confidence: ${confPercent}%</span>
        </div>
        <div class="tags">${tags}</div>
        <div class="confidence-bar">
          <div class="confidence-fill" style="width:${confPercent}%;background:${confColor}"></div>
        </div>
      </div>
      <div class="actions">
        <button class="btn btn-keep" onclick="clipAction('keep')">
          Keep <span class="shortcut">K</span>
        </button>
        <button class="btn btn-skip" onclick="clipAction('skip')">
          Skip <span class="shortcut">S</span>
        </button>
        <button class="btn btn-discard" onclick="clipAction('discard')">
          Discard <span class="shortcut">D</span>
        </button>
      </div>
    </div>
    <div class="keyboard-hint">
      <kbd>K</kbd> keep &nbsp; <kbd>S</kbd> skip &nbsp; <kbd>D</kbd> discard &nbsp; <kbd>Space</kbd> play/pause
    </div>
  `;
}

async function clipAction(type) {
  const clip = clips[currentIndex];

  if (type === 'keep') {
    await fetch(`/api/clips/${clip.video_stem}/${clip.filename}/keep`, {method: 'POST'});
    results[currentIndex] = 'kept';
  } else if (type === 'discard') {
    await fetch(`/api/clips/${clip.video_stem}/${clip.filename}/discard`, {method: 'POST'});
    results[currentIndex] = 'discarded';
  } else {
    results[currentIndex] = 'skipped';
  }

  currentIndex++;
  showClip();
}

function showDone() {
  const kept = results.filter(r => r === 'kept').length;
  const discarded = results.filter(r => r === 'discarded').length;
  const skipped = results.filter(r => r === 'skipped').length;

  document.getElementById('headerRight').textContent = 'Done';
  document.getElementById('reviewContent').innerHTML = `
    <div class="done-state">
      <h2>Review Complete</h2>
      <p>${kept} kept &middot; ${discarded} discarded &middot; ${skipped} skipped</p>
    </div>
  `;
}

function fmtTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}

// ---- Load defaults ----
fetch('/api/defaults').then(r => r.json()).then(data => {
  document.getElementById('folderPath').value = data.folder;
});

// ---- Keyboard shortcuts (only active on Review tab) ----
document.addEventListener('keydown', (e) => {
  if (activeTab !== 'review') return;
  if (currentIndex >= clips.length) return;
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

  switch(e.key.toLowerCase()) {
    case 'k': e.preventDefault(); clipAction('keep'); break;
    case 'd': e.preventDefault(); clipAction('discard'); break;
    case 's': e.preventDefault(); clipAction('skip'); break;
    case ' ':
      e.preventDefault();
      const player = document.getElementById('player');
      if (player) player.paused ? player.play() : player.pause();
      break;
  }
});
</script>
</body>
</html>
"""
