# Process Page Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a folder scan panel to the Process tab that lists videos, their size/age/processing status, and surfaces stale files (30+ days old, configurable) with per-file delete actions.

**Architecture:** New `GET /api/folder-scan` and `POST /api/folder-scan/file/delete` endpoints in the existing process router. Frontend stores last scan result client-side; threshold change re-filters stale without a re-scan. Auto-scans on page load with the default folder; Scan button re-scans on demand.

**Tech Stack:** Python/FastAPI (backend), TypeScript/Vite (frontend), existing `api.ts` + `process.ts` + `index.html` patterns.

---

## File Map

| File | Change |
|------|--------|
| `clipcutter/routes/process.py` | Add `GET /api/folder-scan` and `POST /api/folder-scan/file/delete` |
| `clipcutter/static/src/api.ts` | Add `VideoEntry`, `FolderScanResult` types + `fetchFolderScan`, `deleteFolderFile` |
| `clipcutter/static/index.html` | Add `.btn-scan` CSS, Scan button in form row, `#folderScanPanel` div |
| `clipcutter/static/src/tabs/process.ts` | Add scan logic, render functions, threshold filter, delete handler |
| `tests/test_process.py` | Add `TestFolderScan` and `TestFolderFileDelete` test classes |

---

## Task 1: GET /api/folder-scan endpoint

**Files:**
- Modify: `clipcutter/routes/process.py`
- Test: `tests/test_process.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_process.py` after the existing imports:

```python
import os
import time
```

Add this class at the bottom of `tests/test_process.py`:

```python
class TestFolderScan:
    """GET /api/folder-scan — scan a folder for video files."""

    def test_folder_not_found_returns_400(self, output_dir, app_client):
        resp = app_client.get("/api/folder-scan?folder=/nonexistent/path/xyz")
        assert resp.status_code == 400

    def test_empty_folder_returns_empty_list(self, output_dir, app_client, tmp_path):
        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["videos"] == []
        assert data["total_size_mb"] == 0.0

    def test_unprocessed_video_has_unprocessed_status(self, output_dir, app_client, tmp_path):
        # Create a dummy video file (non-video ext files are ignored)
        video = tmp_path / "clip_001.mp4"
        video.write_bytes(b"\x00" * 1024)

        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["videos"]) == 1
        v = data["videos"][0]
        assert v["filename"] == "clip_001.mp4"
        assert v["status"] == "unprocessed"
        assert v["size_mb"] > 0
        assert v["age_days"] >= 0

    def test_non_video_files_excluded(self, output_dir, app_client, tmp_path):
        (tmp_path / "notes.txt").write_text("hello")
        (tmp_path / "thumb.jpg").write_bytes(b"\xff")
        (tmp_path / "game.mp4").write_bytes(b"\x00" * 512)

        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        data = resp.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["filename"] == "game.mp4"

    def test_processed_video_has_processed_status(self, output_dir, app_client, tmp_path):
        from tests.conftest import save_test_metadata
        from clipcutter.models import ClipMetadata

        video = tmp_path / "session_001.mp4"
        video.write_bytes(b"\x00" * 512)

        clip = ClipMetadata(
            filename="clip_001.mp4",
            source_video=str(video),
            start_time=0.0, end_time=5.0, duration=5.0,
            detection_reasons=["volume_spike"], confidence=0.8,
            status="kept",
        )
        save_test_metadata(output_dir, "session_001", [clip], str(video))

        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        data = resp.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["status"] == "processed"

    def test_pending_review_video_has_pending_review_status(self, output_dir, app_client, tmp_path):
        from tests.conftest import save_test_metadata, create_pending_clip

        video = tmp_path / "session_002.mp4"
        video.write_bytes(b"\x00" * 512)

        clip = create_pending_clip(output_dir, "session_002", "clip_001.mp4",
                                   source_video=str(video))
        save_test_metadata(output_dir, "session_002", [clip], str(video))

        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        data = resp.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["status"] == "pending_review"

    def test_total_size_sums_all_videos(self, output_dir, app_client, tmp_path):
        (tmp_path / "a.mp4").write_bytes(b"\x00" * 1024 * 1024)       # 1 MB
        (tmp_path / "b.mkv").write_bytes(b"\x00" * 2 * 1024 * 1024)   # 2 MB

        resp = app_client.get(f"/api/folder-scan?folder={tmp_path}")
        data = resp.json()
        assert len(data["videos"]) == 2
        assert data["total_size_mb"] == pytest.approx(3.0, abs=0.1)
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_process.py::TestFolderScan -v
```

Expected: all 7 tests fail with `404 Not Found` (endpoint doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

Add to `clipcutter/routes/process.py`, after the existing imports:

```python
import time
from datetime import datetime
```

Add this constant and endpoint inside `create_router`, after the `processing_status` endpoint:

```python
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```
pytest tests/test_process.py::TestFolderScan -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_process.py clipcutter/routes/process.py
git commit -m "feat: add GET /api/folder-scan endpoint"
```

---

## Task 2: POST /api/folder-scan/file/delete endpoint

**Files:**
- Modify: `clipcutter/routes/process.py`
- Test: `tests/test_process.py`

- [ ] **Step 1: Write failing tests**

Add this class at the bottom of `tests/test_process.py`:

```python
class TestFolderFileDelete:
    """POST /api/folder-scan/file/delete — delete a source video file."""

    def test_file_not_found_returns_404(self, output_dir, app_client, tmp_path):
        resp = app_client.post("/api/folder-scan/file/delete", json={
            "folder": str(tmp_path),
            "filename": "nonexistent.mp4",
        })
        assert resp.status_code == 404

    def test_path_traversal_returns_400(self, output_dir, app_client, tmp_path):
        resp = app_client.post("/api/folder-scan/file/delete", json={
            "folder": str(tmp_path),
            "filename": "../outside.mp4",
        })
        assert resp.status_code == 400

    def test_pending_clips_blocks_delete(self, output_dir, app_client, tmp_path):
        from tests.conftest import save_test_metadata, create_pending_clip

        video = tmp_path / "session_003.mp4"
        video.write_bytes(b"\x00" * 512)

        clip = create_pending_clip(output_dir, "session_003", "clip_001.mp4",
                                   source_video=str(video))
        save_test_metadata(output_dir, "session_003", [clip], str(video))

        resp = app_client.post("/api/folder-scan/file/delete", json={
            "folder": str(tmp_path),
            "filename": "session_003.mp4",
        })
        assert resp.status_code == 400
        assert video.exists()  # File was not deleted

    def test_delete_unprocessed_video(self, output_dir, app_client, tmp_path):
        video = tmp_path / "old_game.mp4"
        video.write_bytes(b"\x00" * 1024 * 1024)  # 1 MB

        resp = app_client.post("/api/folder-scan/file/delete", json={
            "folder": str(tmp_path),
            "filename": "old_game.mp4",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["freed_mb"] == pytest.approx(1.0, abs=0.1)
        assert not video.exists()

    def test_delete_processed_video(self, output_dir, app_client, tmp_path):
        from tests.conftest import save_test_metadata
        from clipcutter.models import ClipMetadata

        video = tmp_path / "session_done.mp4"
        video.write_bytes(b"\x00" * 512)

        clip = ClipMetadata(
            filename="clip_001.mp4", source_video=str(video),
            start_time=0.0, end_time=5.0, duration=5.0,
            detection_reasons=["volume_spike"], confidence=0.8,
            status="kept",
        )
        save_test_metadata(output_dir, "session_done", [clip], str(video))

        resp = app_client.post("/api/folder-scan/file/delete", json={
            "folder": str(tmp_path),
            "filename": "session_done.mp4",
        })
        assert resp.status_code == 200
        assert not video.exists()
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_process.py::TestFolderFileDelete -v
```

Expected: all 5 tests fail with `404 Not Found`.

- [ ] **Step 3: Implement the endpoint**

Add this Pydantic model just before `create_router` in `clipcutter/routes/process.py`:

```python
class FolderFileDeleteRequest(BaseModel):
    folder: str
    filename: str
```

Add this endpoint inside `create_router`, after the `scan_folder` endpoint:

```python
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
            clips = load_metadata(meta_path)
            if any(c.status == "pending" for c in clips):
                raise HTTPException(400, "Cannot delete: some clips are still pending review")

        size_mb = round(file_path.stat().st_size / (1024 * 1024), 1)
        file_path.unlink()
        return {"status": "deleted", "freed_mb": size_mb}
```

- [ ] **Step 4: Run tests to confirm they pass**

```
pytest tests/test_process.py::TestFolderScan tests/test_process.py::TestFolderFileDelete -v
```

Expected: all 12 new tests pass.

- [ ] **Step 5: Commit**

```bash
git add clipcutter/routes/process.py tests/test_process.py
git commit -m "feat: add POST /api/folder-scan/file/delete endpoint"
```

---

## Task 3: Frontend types and API helpers

**Files:**
- Modify: `clipcutter/static/src/api.ts`

- [ ] **Step 1: Add types and API functions**

In `clipcutter/static/src/api.ts`, add these types after the `SourceVideo` interface (around line 129):

```typescript
export interface VideoEntry {
  filename: string;
  size_mb: number;
  age_days: number;
  status: 'processed' | 'pending_review' | 'unprocessed';
}

export interface FolderScanResult {
  videos: VideoEntry[];
  total_size_mb: number;
}
```

In the same file, add these two functions in the `// ---- Process ----` section (after `fetchProcessStatus`):

```typescript
export const fetchFolderScan = (folder: string) =>
  apiGet<FolderScanResult>(`/api/folder-scan?folder=${encodeURIComponent(folder)}`);
export const deleteFolderFile = (folder: string, filename: string) =>
  apiPost<{ status: string; freed_mb: number }>('/api/folder-scan/file/delete', { folder, filename });
```

- [ ] **Step 2: Verify TypeScript compiles**

```
cd clipcutter/static && npm run build 2>&1 | tail -20
```

Expected: no TypeScript errors, build succeeds.

- [ ] **Step 3: Commit**

```bash
git add clipcutter/static/src/api.ts
git commit -m "feat: add FolderScanResult types and API helpers"
```

---

## Task 4: HTML structure — Scan button and scan panel

**Files:**
- Modify: `clipcutter/static/index.html`

- [ ] **Step 1: Add CSS for Scan button and scan panels**

In `clipcutter/static/index.html`, add these styles inside the `<style>` block, after the `.btn-process:disabled` rule (around line 89):

```css
  .btn-scan {
    padding: 10px 20px;
    background: #1e293b;
    color: #94a3b8;
    border: 1px solid #334155;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
    white-space: nowrap;
  }
  .btn-scan:hover { background: #273549; color: #e2e8f0; }
  .btn-scan:disabled { background: #111; color: #444; cursor: not-allowed; }

  .scan-panel {
    background: #111;
    border: 1px solid #222;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 16px;
  }
  .scan-panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }
  .scan-panel-title {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #64748b;
  }
  .scan-panel-title.stale { color: #f87171; }
  .scan-panel-meta { font-size: 12px; color: #475569; }
  .scan-panel-error { font-size: 13px; color: #f87171; margin-bottom: 10px; }
  .scan-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  .scan-table th {
    text-align: left;
    color: #475569;
    font-weight: 500;
    padding: 3px 0;
    border-bottom: 1px solid #1e293b;
  }
  .scan-table td { padding: 4px 0; color: #94a3b8; }
  .scan-table td.filename { color: #e2e8f0; }
  .scan-status-processed { color: #4ade80; }
  .scan-status-pending { color: #facc15; }
  .scan-status-unprocessed { color: #f87171; }
  .scan-stale-unprocessed { color: #fca5a5; }
  .scan-stale-processed { color: #fdba74; }
  .btn-delete-file {
    padding: 2px 10px;
    background: #7f1d1d;
    color: #fca5a5;
    border: none;
    border-radius: 4px;
    font-size: 11px;
    cursor: pointer;
    transition: background 0.15s;
  }
  .btn-delete-file:hover { background: #991b1b; }
  .btn-delete-file:disabled { background: #2d1111; color: #6b3333; cursor: not-allowed; }
  .stale-threshold-row {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: #475569;
  }
  .stale-threshold-row input {
    width: 52px;
    padding: 3px 6px;
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 4px;
    color: #e2e8f0;
    font-size: 12px;
    text-align: center;
  }
```

- [ ] **Step 2: Add Scan button to the form row**

In `clipcutter/static/index.html`, replace:

```html
        <button class="btn-process" id="btnProcess" onclick="window._cc.startProcessingHandler()">Process</button>
```

with:

```html
        <button class="btn-scan" id="btnScan" onclick="window._cc.scanFolderHandler()">Scan</button>
        <button class="btn-process" id="btnProcess" onclick="window._cc.startProcessingHandler()">Process</button>
```

- [ ] **Step 3: Add scan panel div**

In `clipcutter/static/index.html`, replace:

```html
    <div class="log-box" id="logBox"></div>
```

with:

```html
    <div id="folderScanPanel" style="display:none">
      <div class="scan-panel" id="videosInFolderSection"></div>
      <div class="scan-panel" id="staleCandidatesSection" style="border-color:#2d1111"></div>
    </div>
    <div class="log-box" id="logBox"></div>
```

- [ ] **Step 4: Verify page loads without JS errors**

Build the frontend and open the app. The Process tab should look unchanged (scan panel is hidden).

```
cd clipcutter/static && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add clipcutter/static/index.html
git commit -m "feat: add scan button and folder scan panel to process tab HTML"
```

---

## Task 5: Frontend scan logic in process.ts

**Files:**
- Modify: `clipcutter/static/src/tabs/process.ts`

- [ ] **Step 1: Replace the full contents of process.ts**

```typescript
import { fetchDefaults, startProcessing, fetchProcessStatus, fetchFolderScan, deleteFolderFile, VideoEntry, FolderScanResult } from '../api';
import { escapeHtml } from '../utils';

let pollTimer: ReturnType<typeof setInterval> | null = null;
let lastScanResult: FolderScanResult | null = null;
let lastScanFolder = '';

export async function initProcessTab(): Promise<void> {
  try {
    const data = await fetchDefaults();
    const folderInput = document.getElementById('folderPath') as HTMLInputElement | null;
    if (folderInput) {
      folderInput.value = data.folder;
      await scanFolder(data.folder);
    }
  } catch (e) {
    console.error('Failed to load defaults:', e);
  }
}

export async function scanFolderHandler(): Promise<void> {
  const folderInput = document.getElementById('folderPath') as HTMLInputElement | null;
  const folder = folderInput?.value.trim() ?? '';
  if (!folder) { alert('Enter a folder path.'); return; }
  await scanFolder(folder);
}

async function scanFolder(folder: string): Promise<void> {
  const btn = document.getElementById('btnScan') as HTMLButtonElement | null;
  if (btn) { btn.disabled = true; btn.textContent = 'Scanning...'; }
  clearScanError();

  try {
    const result = await fetchFolderScan(folder);
    lastScanResult = result;
    lastScanFolder = folder;
    renderVideosInFolder(result);
    const threshold = getThreshold();
    renderStaleCandidates(result, threshold);
    document.getElementById('folderScanPanel')!.style.display = 'block';
  } catch (e) {
    showScanError((e as Error).message);
    document.getElementById('folderScanPanel')!.style.display = 'none';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Scan'; }
  }
}

function getThreshold(): number {
  const input = document.getElementById('staleThreshold') as HTMLInputElement | null;
  const val = parseInt(input?.value ?? '30', 10);
  return isNaN(val) || val < 1 ? 30 : val;
}

function renderVideosInFolder(result: FolderScanResult): void {
  const section = document.getElementById('videosInFolderSection')!;
  if (result.videos.length === 0) {
    section.innerHTML = `
      <div class="scan-panel-header">
        <span class="scan-panel-title">Videos in Folder</span>
        <span class="scan-panel-meta">No video files found</span>
      </div>`;
    return;
  }

  const totalGb = (result.total_size_mb / 1024).toFixed(2);
  const rows = result.videos.map(v => {
    const [statusClass, statusText] = statusDisplay(v.status);
    return `<tr>
      <td class="filename">${escapeHtml(v.filename)}</td>
      <td>${formatSize(v.size_mb)}</td>
      <td>${v.age_days}d</td>
      <td class="${statusClass}">${statusText}</td>
    </tr>`;
  }).join('');

  section.innerHTML = `
    <div class="scan-panel-header">
      <span class="scan-panel-title">Videos in Folder</span>
      <span class="scan-panel-meta">${result.videos.length} video${result.videos.length !== 1 ? 's' : ''} &middot; ${totalGb} GB</span>
    </div>
    <table class="scan-table">
      <thead><tr><th>Filename</th><th>Size</th><th>Age</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderStaleCandidates(result: FolderScanResult, thresholdDays: number): void {
  const section = document.getElementById('staleCandidatesSection')!;
  const stale = result.videos.filter(v =>
    v.age_days >= thresholdDays && v.status !== 'pending_review'
  );

  if (stale.length === 0) {
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';
  const totalMb = stale.reduce((sum, v) => sum + v.size_mb, 0);
  const totalGb = (totalMb / 1024).toFixed(2);

  const rows = stale.map(v => {
    const category = v.status === 'unprocessed' ? 'unprocessed' : 'processed, kept';
    const rowClass = v.status === 'unprocessed' ? 'scan-stale-unprocessed' : 'scan-stale-processed';
    return `<tr>
      <td class="filename ${rowClass}">${escapeHtml(v.filename)}</td>
      <td>${formatSize(v.size_mb)}</td>
      <td style="color:#f87171">${v.age_days}d</td>
      <td style="color:#64748b">${category}</td>
      <td><button class="btn-delete-file" data-filename="${escapeHtml(v.filename)}" onclick="window._cc.deleteFileHandler(this)">Delete</button></td>
    </tr>`;
  }).join('');

  section.innerHTML = `
    <div class="scan-panel-header">
      <span class="scan-panel-title stale">Stale Candidates</span>
      <div class="stale-threshold-row">
        Threshold: <input id="staleThreshold" type="number" value="${thresholdDays}" min="1" oninput="window._cc.thresholdChangedHandler()" />
        days &nbsp;&middot;&nbsp; ${stale.length} file${stale.length !== 1 ? 's' : ''} &middot; ${totalGb} GB
      </div>
    </div>
    <table class="scan-table">
      <thead><tr><th>Filename</th><th>Size</th><th>Age</th><th>Category</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

export function thresholdChangedHandler(): void {
  if (!lastScanResult) return;
  renderStaleCandidates(lastScanResult, getThreshold());
}

export async function deleteFileHandler(btn: HTMLButtonElement): Promise<void> {
  const filename = btn.dataset.filename ?? '';
  if (!filename || !lastScanFolder) return;
  btn.disabled = true;
  btn.textContent = '...';

  try {
    await deleteFolderFile(lastScanFolder, filename);
    await scanFolder(lastScanFolder);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Delete';
    btn.insertAdjacentHTML('afterend', ` <span style="color:#f87171;font-size:11px">${escapeHtml((e as Error).message)}</span>`);
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
        // Re-scan after processing completes so statuses update
        const folder = (document.getElementById('folderPath') as HTMLInputElement)?.value.trim();
        if (folder) await scanFolder(folder);
      }
      box.scrollTop = box.scrollHeight;
    }
  } catch (e) {
    console.error('Poll error:', e);
  }
}

function statusDisplay(status: VideoEntry['status']): [string, string] {
  switch (status) {
    case 'processed':    return ['scan-status-processed', '✓ processed'];
    case 'pending_review': return ['scan-status-pending', '⏳ pending review'];
    case 'unprocessed':  return ['scan-status-unprocessed', '✗ unprocessed'];
  }
}

function formatSize(mb: number): string {
  if (mb >= 1024) return (mb / 1024).toFixed(1) + ' GB';
  return mb.toFixed(0) + ' MB';
}

function clearScanError(): void {
  const existing = document.getElementById('scanError');
  if (existing) existing.remove();
}

function showScanError(msg: string): void {
  clearScanError();
  const form = document.querySelector('.form-section')!;
  const err = document.createElement('div');
  err.id = 'scanError';
  err.className = 'scan-panel-error';
  err.style.marginTop = '8px';
  err.textContent = msg;
  form.appendChild(err);
}
```

- [ ] **Step 2: Wire new exports into main.ts**

Check `clipcutter/static/src/main.ts` to see how `window._cc` is populated. Add the three new handlers (`scanFolderHandler`, `thresholdChangedHandler`, `deleteFileHandler`) to the `window._cc` object.

Open `clipcutter/static/src/main.ts` and find the `window._cc` assignment. It should look something like:

```typescript
window._cc = {
  startProcessingHandler,
  // ...other handlers
};
```

Add the three new imports at the top of `main.ts`:

```typescript
import { initProcessTab, startProcessingHandler, scanFolderHandler, thresholdChangedHandler, deleteFileHandler } from './tabs/process';
```

And add them to `window._cc`:

```typescript
  scanFolderHandler,
  thresholdChangedHandler,
  deleteFileHandler,
```

- [ ] **Step 3: Build and verify**

```
cd clipcutter/static && npm run build 2>&1 | tail -20
```

Expected: build succeeds with no TypeScript errors.

- [ ] **Step 4: Manual smoke test**

Start the app (`python -m clipcutter ui`) and verify:
1. Process tab loads and auto-scans the launch folder
2. Video list appears with filename, size, age, status
3. Changing the folder path and clicking Scan updates the list
4. Stale panel only appears when videos older than threshold exist
5. Changing the threshold input re-filters the stale list without a page reload
6. Delete button removes the file and refreshes the scan

- [ ] **Step 5: Commit**

```bash
git add clipcutter/static/src/tabs/process.ts clipcutter/static/src/main.ts
git commit -m "feat: add folder scan panel with video list and stale candidates to process tab"
```

---

## Task 6: Full test run

- [ ] **Step 1: Run the full test suite**

```
pytest tests/ -v -k "not browser"
```

Expected: all existing tests plus the 12 new tests pass. Zero failures.

- [ ] **Step 2: If any failures, fix them before proceeding**

- [ ] **Step 3: Final commit if any fixes were made**

```bash
git add -p
git commit -m "fix: address test failures from folder scan feature"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Auto-scan on page load with default folder → Task 5 `initProcessTab`
- ✅ Scan button re-scans on demand → Task 5 `scanFolderHandler`, Task 4 button
- ✅ Videos panel: filename, size, age, status → Task 5 `renderVideosInFolder`
- ✅ Videos panel header: count + total GB → Task 5 `renderVideosInFolder`
- ✅ Stale panel only shown if stale count > 0 → Task 5 `renderStaleCandidates` (`display:none`)
- ✅ Stale header: count + total GB + threshold input → Task 5 `renderStaleCandidates`
- ✅ Threshold re-filters client-side → Task 5 `thresholdChangedHandler`
- ✅ Default threshold 30 days → Task 5 `getThreshold` default
- ✅ Row colors: red = unprocessed, amber = processed_kept → Task 5 CSS classes
- ✅ Delete button → Task 5 `deleteFileHandler`, Task 2 endpoint
- ✅ Delete re-scans after success → Task 5 `deleteFileHandler` calls `scanFolder`
- ✅ Delete inline error on failure → Task 5 error handling in `deleteFileHandler`
- ✅ Path traversal guard → Task 2 `relative_to` check
- ✅ Pending clips block delete → Task 2 guard
- ✅ Panels hidden before first scan → Task 4 `style="display:none"`
- ✅ Auto-re-scan after processing completes → Task 5 `pollStatus` success branch
- ✅ No new files created → all changes in existing files

**Type consistency check:**
- `VideoEntry` defined in Task 3, used in Task 5 — ✅ matches
- `FolderScanResult` defined in Task 3, used in Task 5 — ✅ matches
- `fetchFolderScan` returns `FolderScanResult` in Task 3, consumed correctly in Task 5 — ✅
- `deleteFolderFile(folder, filename)` in Task 3 matches usage in Task 5 — ✅
- Backend returns `{ videos, total_size_mb }` in Task 1, matches `FolderScanResult` in Task 3 — ✅
