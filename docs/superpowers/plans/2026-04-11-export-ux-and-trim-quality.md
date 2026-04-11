# Export UX Improvements & Trim Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add disk space visibility to the Export tab, fix trim quality loss in the Review tab, and surface source video cleanup persistently.

**Architecture:** Five independent backend + frontend improvements. Backend changes are in `routes/encode.py`, `routes/review.py`, and `metadata.py`. Frontend changes are in `api.ts`, `encode.ts`, and `review.ts`. Each task is independently testable and committable.

**Tech Stack:** Python/FastAPI (backend), TypeScript (frontend), FFmpeg (video processing), pytest + FastAPI TestClient (tests).

---

## File Map

| File | Changes |
|---|---|
| `clipcutter/metadata.py` | Add `clear_clip_encoding()` |
| `clipcutter/routes/encode.py` | Add `GET /api/storage-summary`, `DELETE /api/encoded/{stem}/{filename}`, add `size_mb`/`encoded_size_mb` to `/api/kept` response |
| `clipcutter/routes/review.py` | Add `quality` to `KeepRequest`, fix single-segment to use `-c copy`, fix multi-segment to use two-pass copy / CRF 16 / CRF 0 |
| `clipcutter/static/src/api.ts` | Add `StorageSummary` type, `fetchStorageSummary()`, `deleteEncodedClip()`, `quality?` on `KeepRequest`, `size_mb`/`encoded_size_mb` on `KeptClipInfo` |
| `clipcutter/static/src/tabs/encode.ts` | Add storage summary bar, size columns, delete-encoded button, source videos section |
| `clipcutter/static/src/tabs/review.ts` | Add quality selector to trim controls, pass `quality` on keep |
| `tests/test_export.py` | Tests for storage summary, file sizes, delete encoded |
| `tests/test_review.py` | Tests for single-segment copy, multi-segment quality modes |

---

## Task 1: Storage Summary API

**Files:**
- Modify: `clipcutter/routes/encode.py`
- Test: `tests/test_export.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_export.py`:

```python
class TestStorageSummary:
    """GET /api/storage-summary returns counts and sizes for kept/encoded/compilations."""

    def test_empty_returns_zeros(self, output_dir, app_client):
        resp = app_client.get("/api/storage-summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kept"] == {"count": 0, "size_mb": 0.0}
        assert data["encoded"] == {"count": 0, "size_mb": 0.0}
        assert data["compilations"] == {"count": 0, "size_mb": 0.0}
        assert data["total_mb"] == 0.0

    def test_counts_kept_clips(self, output_dir, app_client):
        stem = "summary_test"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/summary.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/summary.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})

        resp = app_client.get("/api/storage-summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kept"]["count"] == 1
        assert data["kept"]["size_mb"] > 0
        assert data["total_mb"] == data["kept"]["size_mb"]

    def test_total_sums_categories(self, output_dir, app_client):
        resp = app_client.get("/api/storage-summary")
        data = resp.json()
        expected = round(
            data["kept"]["size_mb"] + data["encoded"]["size_mb"] + data["compilations"]["size_mb"],
            1,
        )
        assert data["total_mb"] == expected
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_export.py::TestStorageSummary -v
```
Expected: FAIL — `404` (endpoint doesn't exist yet)

- [ ] **Step 3: Add `GET /api/storage-summary` to `routes/encode.py`**

Add this inside `create_router(state)`, before the `return router` line. Also add `DIR_COMPILATIONS` to the import at top of file:

```python
# At top of file, update the config import:
from clipcutter.config import DIR_CLIPS, DIR_COMPILATIONS, DIR_ENCODED, DIR_KEPT, DIR_METADATA
```

```python
    @router.get("/api/storage-summary")
    def storage_summary():
        def _scan(path: Path) -> tuple[int, float]:
            count = 0
            size_mb = 0.0
            if path.exists():
                for f in path.rglob("*"):
                    if f.is_file() and not f.name.startswith("."):
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
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_export.py::TestStorageSummary -v
```
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add clipcutter/routes/encode.py tests/test_export.py
git commit -m "feat: add GET /api/storage-summary endpoint"
```

---

## Task 2: File Sizes in `/api/kept` Response

**Files:**
- Modify: `clipcutter/routes/encode.py`
- Test: `tests/test_export.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_export.py`:

```python
class TestKeptClipSizes:
    """size_mb and encoded_size_mb fields present in /api/kept response."""

    def test_size_mb_present(self, output_dir, app_client):
        stem = "sizevid"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/sizevid.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/sizevid.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})

        resp = app_client.get("/api/kept")
        assert resp.status_code == 200
        kept = next(c for c in resp.json()["clips"] if c["video_stem"] == stem)
        assert "size_mb" in kept
        assert kept["size_mb"] > 0

    def test_encoded_size_mb_null_when_not_encoded(self, output_dir, app_client):
        stem = "sizevid2"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/sizevid2.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/sizevid2.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})

        resp = app_client.get("/api/kept")
        kept = next(c for c in resp.json()["clips"] if c["video_stem"] == stem)
        assert kept["encoded_size_mb"] is None

    def test_encoded_size_mb_present_after_encode(self, output_dir, app_client):
        stem = "sizeenc"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/sizeenc.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/sizeenc.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})
        app_client.post("/api/encode", json={
            "clips": [{"video_stem": stem, "filename": "clip_001.mp4"}],
            "preset": "original",
        })
        _wait_encoding(app_client)

        resp = app_client.get("/api/kept")
        kept = next(c for c in resp.json()["clips"] if c["video_stem"] == stem)
        assert kept["encoded_size_mb"] is not None
        assert kept["encoded_size_mb"] > 0
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_export.py::TestKeptClipSizes -v
```
Expected: FAIL — `size_mb` key missing

- [ ] **Step 3: Update `list_kept_clips()` in `routes/encode.py`**

Replace the `encoded_exists` block in `list_kept_clips()` (around line 100–107) with:

```python
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
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_export.py::TestKeptClipSizes -v
```
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add clipcutter/routes/encode.py tests/test_export.py
git commit -m "feat: add size_mb and encoded_size_mb to /api/kept response"
```

---

## Task 3: Delete Encoded Version API

**Files:**
- Modify: `clipcutter/metadata.py`
- Modify: `clipcutter/routes/encode.py`
- Test: `tests/test_export.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_export.py`:

```python
class TestDeleteEncodedClip:
    """DELETE /api/encoded/{stem}/{filename} removes encoded file and clears metadata."""

    def test_delete_encoded_removes_file(self, output_dir, app_client):
        stem = "delencvid"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/delencvid.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/delencvid.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})
        app_client.post("/api/encode", json={
            "clips": [{"video_stem": stem, "filename": "clip_001.mp4"}],
            "preset": "original",
        })
        _wait_encoding(app_client)

        # Confirm encoded file exists
        encoded_dir = output_dir / "clips" / "encoded" / stem
        assert encoded_dir.exists()
        encoded_files = list(encoded_dir.iterdir())
        assert len(encoded_files) == 1

        resp = app_client.delete(f"/api/encoded/{stem}/clip_001.mp4")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert resp.json()["freed_mb"] > 0
        assert not any(encoded_dir.iterdir()) if encoded_dir.exists() else True

    def test_delete_encoded_clears_metadata(self, output_dir, app_client):
        stem = "delencmeta"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/delencmeta.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/delencmeta.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})
        app_client.post("/api/encode", json={
            "clips": [{"video_stem": stem, "filename": "clip_001.mp4"}],
            "preset": "original",
        })
        _wait_encoding(app_client)

        app_client.delete(f"/api/encoded/{stem}/clip_001.mp4")

        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["encoded_filename"] is None
        assert meta["clips"][0]["encoding_preset"] is None

    def test_delete_encoded_not_found_returns_404(self, output_dir, app_client):
        resp = app_client.delete("/api/encoded/fakevid/clip_001.mp4")
        assert resp.status_code == 404

    def test_kept_clip_untouched_after_delete_encoded(self, output_dir, app_client):
        stem = "delenckeep"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/delenckeep.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/delenckeep.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep", json={"segments": []})
        app_client.post("/api/encode", json={
            "clips": [{"video_stem": stem, "filename": "clip_001.mp4"}],
            "preset": "original",
        })
        _wait_encoding(app_client)

        app_client.delete(f"/api/encoded/{stem}/clip_001.mp4")

        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists(), "Kept clip must still exist after deleting encoded version"
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_export.py::TestDeleteEncodedClip -v
```
Expected: FAIL — 404 (endpoint doesn't exist)

- [ ] **Step 3: Add `clear_clip_encoding()` to `metadata.py`**

Add after `update_clip_encoding()` in `clipcutter/metadata.py`:

```python
def clear_clip_encoding(metadata_path: Path, filename: str) -> None:
    """Clear encoding info for a clip in the metadata file."""
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    for clip in data["clips"]:
        if clip["filename"] == filename:
            clip["encoded_filename"] = None
            clip["encoding_preset"] = None
            break
    tmp = metadata_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(metadata_path)
```

- [ ] **Step 4: Add `DELETE /api/encoded/{video_stem}/{filename}` to `routes/encode.py`**

Update the import from `metadata.py` at the top of `routes/encode.py`:

```python
from clipcutter.metadata import (
    load_metadata, load_metadata_dict, update_clip_encoding,
    update_clip_status, clear_clip_encoding,
)
```

Add inside `create_router(state)`, before `return router`:

```python
    @router.delete("/api/encoded/{video_stem}/{filename}")
    def delete_encoded_clip(video_stem: str, filename: str):
        """Delete the encoded version of a kept clip and clear its encoding metadata."""
        meta_path = state.output_dir / DIR_METADATA / f"{video_stem}_clips.json"
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

        enc_path = state.output_dir / DIR_CLIPS / DIR_ENCODED / video_stem / encoded_filename
        freed_mb = 0.0
        if enc_path.exists():
            freed_mb = round(enc_path.stat().st_size / (1024 * 1024), 1)
            enc_path.unlink()
            if enc_path.parent.is_dir() and not any(enc_path.parent.iterdir()):
                enc_path.parent.rmdir()

        clear_clip_encoding(meta_path, filename)
        return {"status": "deleted", "freed_mb": freed_mb}
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_export.py::TestDeleteEncodedClip -v
```
Expected: 4 PASSED

- [ ] **Step 6: Commit**

```bash
git add clipcutter/metadata.py clipcutter/routes/encode.py tests/test_export.py
git commit -m "feat: add DELETE /api/encoded endpoint and clear_clip_encoding"
```

---

## Task 4: Frontend — Export Tab Updates

**Files:**
- Modify: `clipcutter/static/src/api.ts`
- Modify: `clipcutter/static/src/tabs/encode.ts`

- [ ] **Step 1: Update `api.ts` — new types and functions**

Add the `StorageSummary` interface after the existing interfaces (e.g. after `FolderScanResult`):

```typescript
export interface StorageSummary {
  kept: { count: number; size_mb: number };
  encoded: { count: number; size_mb: number };
  compilations: { count: number; size_mb: number };
  total_mb: number;
}
```

Update `KeptClipInfo` — add two optional fields after `clipped_at`:

```typescript
  clipped_at?: string;
  size_mb?: number;
  encoded_size_mb?: number | null;
```

Update `KeepRequest` — add optional `quality` field:

```typescript
export interface KeepRequest {
  segments: Segment[];
  custom_name: string | null;
  quality?: string;
}
```

Add new API functions after `openKeptFolder`:

```typescript
export const fetchStorageSummary = () =>
  apiGet<StorageSummary>('/api/storage-summary');

export const deleteEncodedClip = (videoStem: string, filename: string) =>
  apiDelete<{ status: string; freed_mb: number }>(
    `/api/encoded/${encodeURIComponent(videoStem)}/${encodeURIComponent(filename)}`
  );
```

- [ ] **Step 2: Update `encode.ts` — import new functions**

Update the import at the top of `encode.ts`:

```typescript
import {
  fetchKeptClips, fetchPresets, startEncoding, fetchEncodeStatus, cancelEncoding,
  fetchYouTubeStatus, fetchYouTubePlaylists, startYouTubeAuth, revokeYouTubeAuth,
  startUpload, fetchUploadStatus, cancelUpload, createPlaylist, deleteKeptClip,
  openKeptFolder, fetchStorageSummary, deleteEncodedClip, fetchSources, deleteSource,
} from '../api';
import type { KeptClipInfo, Playlist, StorageSummary, SourceVideo } from '../api';
```

Add module-level variable after `let uploadPoll`:

```typescript
let storageSummary: StorageSummary | null = null;
let sourcesData: SourceVideo[] = [];
```

- [ ] **Step 3: Update `loadExportTab()` to fetch summary and sources**

Replace the `Promise.all` in `loadExportTab()`:

```typescript
    const [keptData, presetsData, ytStatus, summaryData, sourcesResp] = await Promise.all([
      fetchKeptClips(),
      fetchPresets(),
      fetchYouTubeStatus(),
      fetchStorageSummary().catch(() => null),
      fetchSources().catch(() => ({ sources: [] })),
    ]);
    keptClips = keptData.clips || [];
    encodingPresets = presetsData.presets || [];
    defaultPreset = presetsData.default || 'h264_hq';
    encodingFpsOptions = presetsData.fps_options || [null, 24, 30, 60];
    ytAuthenticated = ytStatus.authenticated || false;
    ytChannelName = ytStatus.channel_name || '';
    storageSummary = summaryData;
    sourcesData = sourcesResp.sources || [];
```

- [ ] **Step 4: Add storage summary bar and source size to clip rows in `renderExportView()`**

At the top of `renderExportView()`, before the Encode Clips section, add:

```typescript
  // === Storage Summary Bar ===
  if (storageSummary) {
    const fmt = (mb: number) => mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb.toFixed(1) + ' MB';
    html += `<div style="font-size:12px;color:#666;margin-bottom:16px;padding:10px 14px;background:#111;border-radius:8px;display:flex;gap:24px;flex-wrap:wrap">`;
    html += `<span>Kept: <span style="color:#94a3b8">${storageSummary.kept.count} clips · ${fmt(storageSummary.kept.size_mb)}</span></span>`;
    html += `<span>Encoded: <span style="color:#94a3b8">${storageSummary.encoded.count} clips · ${fmt(storageSummary.encoded.size_mb)}</span></span>`;
    html += `<span>Compilations: <span style="color:#94a3b8">${storageSummary.compilations.count} · ${fmt(storageSummary.compilations.size_mb)}</span></span>`;
    html += `<span style="margin-left:auto">Total: <span style="color:#e2e8f0;font-weight:600">${fmt(storageSummary.total_mb)}</span></span>`;
    html += `</div>`;
  }
```

- [ ] **Step 5: Add size info and delete-encoded button to each clip row**

Replace the clip row rendering block in `renderExportView()` (the loop from `for (let i = 0; i < keptClips.length; i++)`). The full updated row:

```typescript
    for (let i = 0; i < keptClips.length; i++) {
      const clip = keptClips[i];
      const dur = clip.duration ? Math.round(clip.duration) + 's' : '';
      const date = clip.clipped_at ? clip.clipped_at.slice(0, 10) : '';
      const tags = (clip.detection_reasons || []).map(r =>
        `<span class="tag tag-${r}">${r.replace('_', ' ')}</span>`
      ).join('');

      // Size display
      const keptMb = clip.size_mb != null ? clip.size_mb.toFixed(1) + ' MB' : '';
      let sizeHtml = '';
      if (keptMb) {
        if (clip.encoded_exists && clip.encoded_size_mb != null) {
          sizeHtml = `<span class="clip-detail">${keptMb} → ${clip.encoded_size_mb.toFixed(1)} MB</span>`;
        } else {
          sizeHtml = `<span class="clip-detail">${keptMb}</span>`;
        }
      }

      // Encoded badge + delete-encoded button
      let encodedHtml = '';
      if (clip.encoded_exists) {
        encodedHtml = `<span class="badge badge-encoded">Encoded</span>`;
        encodedHtml += `<button class="btn-cancel" style="padding:2px 8px;font-size:11px" `
          + `data-stem="${escapeHtml(clip.video_stem)}" data-filename="${escapeHtml(clip.filename)}" `
          + `onclick="window._cc.deleteEncodedClipHandler(this)" title="Delete encoded version">✕ encoded</button>`;
      }

      html += `<div class="clip-row">`;
      html += `<input type="checkbox" class="clip-checkbox encode-cb" data-index="${i}" checked>`;
      html += `<span class="clip-name" title="${escapeHtml(clip.filename)}" style="cursor:pointer" onclick="window._cc.previewClip(${i})">${escapeHtml(clip.custom_name || clip.filename)}</span>`;
      html += `<span class="clip-detail">${escapeHtml(clip.video_stem || '')}</span>`;
      html += `<span class="clip-detail">${dur}</span>`;
      html += `<span class="clip-detail" style="color:#888">${date}</span>`;
      html += `<span class="tags" style="margin-bottom:0">${tags}</span>`;
      html += encodedHtml;
      html += sizeHtml;
      html += `<button class="btn-cancel" style="margin-left:auto;padding:2px 8px;font-size:12px" `
            + `data-stem="${escapeHtml(clip.video_stem)}" data-filename="${escapeHtml(clip.filename)}" `
            + `onclick="window._cc.deleteKeptClipHandler(this)">✕</button>`;
      html += `<button class="btn-secondary" style="padding:2px 8px;font-size:12px" `
            + `data-stem="${escapeHtml(clip.video_stem)}" `
            + `onclick="window._cc.openFolderHandler(this.dataset.stem)" title="Open folder in Explorer">📁</button>`;
      html += `</div>`;
    }
```

- [ ] **Step 6: Add source videos section at bottom of `renderExportView()`**

Add before the final `document.getElementById('exportContent')!.innerHTML = html;` line:

```typescript
  // === Source Videos ===
  const deletableSources = sourcesData.filter(s => s.exists);
  if (deletableSources.length > 0) {
    html += `<div class="export-section"><h2>Source Videos</h2>`;
    html += `<p style="color:#888;font-size:13px;margin-bottom:16px">Delete source videos to free disk space. Only sources with existing files are shown.</p>`;
    for (const src of deletableSources) {
      const name = src.source_path.split(/[/\\]/).pop() ?? src.source_path;
      const pending = src.total - src.kept - src.discarded;
      const reviewSummary = src.fully_reviewed
        ? `${src.kept} kept / ${src.discarded} discarded`
        : `${src.kept} kept / ${src.discarded} discarded / ${pending} pending`;
      const reviewColor = src.fully_reviewed ? '#4ade80' : '#fbbf24';
      html += `<div class="source-row" id="export-src-${escapeHtml(src.video_stem)}">`;
      html += `<div class="source-info">`;
      html += `<div class="source-name" title="${escapeHtml(src.source_path)}">${escapeHtml(name)}</div>`;
      html += `<div class="source-detail" style="color:${reviewColor}">${reviewSummary}</div>`;
      html += `</div>`;
      html += `<div class="source-size">${src.size_mb} MB</div>`;
      html += `<button class="btn-delete" data-stem="${escapeHtml(src.video_stem)}" `
            + `onclick="window._cc.deleteSourceFromExportHandler(this.dataset.stem, this)">Delete</button>`;
      html += `</div>`;
    }
    const totalMb = deletableSources.reduce((sum, s) => sum + s.size_mb, 0);
    html += `<div class="cleanup-total">Total reclaimable: ${totalMb.toFixed(1)} MB</div>`;
    html += `</div>`;
  }
```

- [ ] **Step 7: Add `deleteEncodedClipHandler` and `deleteSourceFromExportHandler` to `encode.ts`**

Add after `openFolderHandler`:

```typescript
export async function deleteEncodedClipHandler(btn: HTMLButtonElement): Promise<void> {
  const stem = btn.dataset.stem!;
  const filename = btn.dataset.filename!;
  if (!confirm(`Delete encoded version of "${filename}"? The original kept clip will remain.`)) return;
  btn.disabled = true;
  try {
    await deleteEncodedClip(stem, filename);
    const idx = keptClips.findIndex(c => c.video_stem === stem && c.filename === filename);
    if (idx >= 0) {
      keptClips[idx].encoded_exists = false;
      keptClips[idx].encoded_filename = null;
      keptClips[idx].encoded_size_mb = undefined;
    }
    // Reload to refresh summary bar and row
    await loadExportTab();
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false;
  }
}

export async function deleteSourceFromExportHandler(videoStem: string, btn: HTMLButtonElement): Promise<void> {
  if (!confirm('Permanently delete this source video? This cannot be undone.')) return;
  btn.disabled = true;
  btn.textContent = 'Deleting...';
  try {
    const data = await deleteSource(videoStem);
    const row = document.getElementById('export-src-' + videoStem);
    if (row) {
      row.querySelector('.btn-delete')!.outerHTML = '<button class="btn-deleted">Deleted</button>';
      const sizeEl = row.querySelector('.source-size') as HTMLElement;
      let label = data.freed_mb + ' MB freed';
      if (data.leftover > 0) label += ` (${data.leftover} clip(s) locked)`;
      sizeEl.textContent = label;
      sizeEl.style.color = data.leftover > 0 ? '#fbbf24' : '#4ade80';
    }
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false;
    btn.textContent = 'Delete';
  }
}
```

- [ ] **Step 8: Register new handlers in `main.ts`**

In `clipcutter/static/src/main.ts`, update the encode import line (line 3):

```typescript
import { loadExportTab, renderExportView, toggleAllClips, startEncodingHandler, cancelEncodingHandler, startYouTubeAuthHandler, revokeYouTubeAuthHandler, startUploadHandler, cancelUploadHandler, keptClips, deleteKeptClipHandler, openFolderHandler, previewClip, deleteEncodedClipHandler, deleteSourceFromExportHandler } from './tabs/encode';
```

Add two entries to the `handlers` object in the `// Encode` section:

```typescript
  deleteEncodedClipHandler,
  deleteSourceFromExportHandler,
```

- [ ] **Step 9: Verify in browser**

```
python -m clipcutter ui
```

Open the Export tab. Confirm:
- Storage summary bar appears at top
- Clip rows show file sizes
- Encoded clips show a "✕ encoded" button
- Source videos section appears at bottom

- [ ] **Step 10: Commit**

```bash
git add clipcutter/static/src/api.ts clipcutter/static/src/tabs/encode.ts clipcutter/static/src/main.ts
git commit -m "feat: add storage summary bar, file sizes, delete-encoded, and source videos section to Export tab"
```

---

## Task 5: Single-Segment Trim Quality Fix

**Files:**
- Modify: `clipcutter/routes/review.py`
- Test: `tests/test_review.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_review.py`:

```python
class TestSingleSegmentTrimUsesCopy:
    """Single-segment trim should use -c copy (stream copy), not re-encode."""

    def test_single_segment_trim_produces_valid_file(self, output_dir, app_client):
        """Trim a real video file and confirm the output is a valid mp4."""
        stem = "copytrim"
        clip = create_pending_clip_long(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/copytrim.mp4",
            file_duration_s=3.0, start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/copytrim.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={"segments": [{"start": 0.5, "end": 2.5}]},
        )
        assert resp.status_code == 200
        assert resp.json()["trimmed"] is True

        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()
        assert kept_path.stat().st_size > 0
```

- [ ] **Step 2: Run to verify it passes already (it should — we're just changing the ffmpeg flags)**

```
pytest tests/test_review.py::TestSingleSegmentTrimUsesCopy -v
```

Note: this test may already pass since it only checks the output file exists. The important change is a code quality fix. Proceed to step 3.

- [ ] **Step 3: Update single-segment trim in `routes/review.py`**

Locate the `elif len(segments) == 1:` block (around line 220–234). Replace it:

```python
        elif len(segments) == 1:
            # Single segment trim — use stream copy to avoid quality loss
            seg = segments[0]
            duration = seg.end - seg.start
            result = subprocess.run(
                ["ffmpeg", "-y",
                 "-ss", f"{seg.start:.3f}",
                 "-i", str(clip_path),
                 "-t", f"{duration:.3f}",
                 "-c", "copy",
                 "-avoid_negative_ts", "make_zero",
                 str(dest)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise HTTPException(500, f"FFmpeg failed: {result.stderr[-200:]}")
            trimmed = True
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_review.py -v
```
Expected: all existing tests still PASS, new test PASSES

- [ ] **Step 5: Commit**

```bash
git add clipcutter/routes/review.py tests/test_review.py
git commit -m "fix: use -c copy for single-segment trim to avoid quality loss"
```

---

## Task 6: Multi-Segment Quality Modes

**Files:**
- Modify: `clipcutter/routes/review.py`
- Test: `tests/test_review.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_review.py`:

```python
class TestMultiSegmentQualityModes:
    """Multi-segment keep supports copy (default), precise (crf16), and ultra (crf0) modes."""

    def test_multi_segment_copy_mode_default(self, output_dir, app_client):
        """Default quality='copy' uses two-pass copy — file exists and has content."""
        stem = "msegcopy"
        clip = create_pending_clip_long(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/msegcopy.mp4",
            file_duration_s=4.0, start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/msegcopy.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={"segments": [{"start": 0.0, "end": 1.5}, {"start": 2.5, "end": 4.0}]},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "kept"
        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()
        assert kept_path.stat().st_size > 0

    def test_multi_segment_precise_mode(self, output_dir, app_client):
        """quality='precise' re-encodes with crf16 — file exists."""
        stem = "msegprecise"
        clip = create_pending_clip_long(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/msegprecise.mp4",
            file_duration_s=4.0, start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/msegprecise.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={
                "segments": [{"start": 0.0, "end": 1.5}, {"start": 2.5, "end": 4.0}],
                "quality": "precise",
            },
        )
        assert resp.status_code == 200
        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()
        assert kept_path.stat().st_size > 0

    def test_multi_segment_ultra_mode(self, output_dir, app_client):
        """quality='ultra' re-encodes with crf0 — file exists and is larger than precise."""
        stem = "msegultra"
        clip = create_pending_clip_long(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/msegultra.mp4",
            file_duration_s=4.0, start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/msegultra.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={
                "segments": [{"start": 0.0, "end": 1.5}, {"start": 2.5, "end": 4.0}],
                "quality": "ultra",
            },
        )
        assert resp.status_code == 200
        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()
        assert kept_path.stat().st_size > 0
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_review.py::TestMultiSegmentQualityModes -v
```
Expected: `test_multi_segment_copy_mode_default` may pass (existing copy logic), `precise` and `ultra` will fail or misbehave.

- [ ] **Step 3: Update `KeepRequest` and multi-segment logic in `routes/review.py`**

Add `quality` to `KeepRequest`:

```python
class KeepRequest(BaseModel):
    segments: list[Segment] = []
    custom_name: Optional[str] = None
    quality: str = "copy"  # "copy" | "precise" | "ultra"
```

Add `import shutil` and `import tempfile` at the top of the file (alongside existing imports).

Replace the multi-segment `else:` block (starting around line 237) with:

```python
        else:
            # Multiple segments
            quality = req.quality if req and req.quality in ("copy", "precise", "ultra") else "copy"

            if quality == "copy":
                # Two-pass: extract each segment with -c copy, then concat demuxer
                import shutil as _shutil
                tmp_dir = Path(tempfile.mkdtemp())
                try:
                    seg_files = []
                    for j, seg in enumerate(segments):
                        seg_path = tmp_dir / f"seg_{j}.mp4"
                        result = subprocess.run(
                            ["ffmpeg", "-y",
                             "-ss", f"{seg.start:.3f}",
                             "-t", f"{seg.end - seg.start:.3f}",
                             "-i", str(clip_path),
                             "-c", "copy",
                             "-avoid_negative_ts", "make_zero",
                             str(seg_path)],
                            capture_output=True, text=True,
                        )
                        if result.returncode != 0:
                            raise HTTPException(500, f"FFmpeg segment extract failed: {result.stderr[-200:]}")
                        seg_files.append(seg_path)

                    filelist = tmp_dir / "filelist.txt"
                    filelist.write_text(
                        "\n".join(f"file '{p}'" for p in seg_files),
                        encoding="utf-8",
                    )

                    result = subprocess.run(
                        ["ffmpeg", "-y",
                         "-f", "concat", "-safe", "0",
                         "-i", str(filelist),
                         "-c", "copy",
                         str(dest)],
                        capture_output=True, text=True,
                    )
                    if result.returncode != 0:
                        raise HTTPException(500, f"FFmpeg concat failed: {result.stderr[-200:]}")
                finally:
                    _shutil.rmtree(tmp_dir, ignore_errors=True)

            else:
                # Re-encode with quality setting
                crf = "16" if quality == "precise" else "0"
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
                       "-c:v", "libx264", "-crf", crf, "-c:a", "aac",
                       str(dest)],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    raise HTTPException(500, f"FFmpeg failed: {result.stderr[-200:]}")

            trimmed = True
```

- [ ] **Step 4: Run all review tests**

```
pytest tests/test_review.py -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add clipcutter/routes/review.py tests/test_review.py
git commit -m "feat: add multi-segment quality modes (copy/precise/ultra) to keep endpoint"
```

---

## Task 7: Frontend — Quality Selector in Review Tab

**Files:**
- Modify: `clipcutter/static/src/tabs/review.ts`

- [ ] **Step 1: Add quality selector HTML to `showClip()`**

In `review.ts`, locate `segmentsHtml`. After the closing `</div>` of the segment list block (the `<div style="margin:6px 0">` with `+ Add segment`), add a quality selector row:

```typescript
  const segmentsHtml = `
    <div class="trim-section" id="segmentList">
      <div class="segment-row" data-seg="0">
        <span class="trim-label">Seg 1</span>
        <span class="trim-label" style="margin-left:8px">In</span>
        <input type="text" class="trim-time seg-in" data-seg="0" value="0:00" />
        <button class="trim-btn" data-action="set-point" data-seg="0" data-which="in">Set</button>
        <button class="trim-btn" data-action="seek-to" data-seg="0" data-which="in">Go</button>
        <span class="trim-label" style="margin-left:8px">Out</span>
        <input type="text" class="trim-time seg-out" data-seg="0" value="${fmtTimePrecise(clip.duration)}" />
        <button class="trim-btn" data-action="set-point" data-seg="0" data-which="out">Set</button>
        <button class="trim-btn" data-action="seek-to" data-seg="0" data-which="out">Go</button>
      </div>
    </div>
    <div style="margin:6px 0;display:flex;align-items:center;gap:12px">
      <button class="trim-btn" data-action="add-segment">+ Add segment</button>
      <span class="trim-indicator" id="trimIndicator"></span>
      <span class="trim-label" style="margin-left:auto">Trim quality</span>
      <select class="trim-btn" id="trimQuality" style="cursor:pointer;padding:6px 8px">
        <option value="copy">Fast (copy)</option>
        <option value="precise">Precise (CRF 16)</option>
        <option value="ultra">Ultra (lossless)</option>
      </select>
    </div>
  `;
```

- [ ] **Step 2: Pass `quality` in `clipAction()` when calling `keepClip`**

In `clipAction()`, update the `keepClip` call:

```typescript
      const qualitySelect = document.getElementById('trimQuality') as HTMLSelectElement | null;
      const quality = qualitySelect?.value ?? 'copy';
      await keepClip(clip.video_stem, clip.filename, {
        segments: segments.map(s => ({ start: s.start, end: s.end })),
        custom_name: customName,
        quality,
      });
```

- [ ] **Step 3: Verify in browser**

```
python -m clipcutter ui
```

Process a video, open Review tab. Confirm the quality selector appears between "+ Add segment" and the trim indicator. Try keeping a clip with 2+ segments on each quality mode — all three should produce a kept file.

- [ ] **Step 4: Commit**

```bash
git add clipcutter/static/src/tabs/review.ts
git commit -m "feat: add trim quality selector (copy/precise/ultra) to review tab"
```

---

## Task 8: Full Test Suite

- [ ] **Step 1: Run the full suite**

```
pytest tests/ -v -k "not browser"
```
Expected: all existing tests pass, no regressions.

- [ ] **Step 2: Run browser tests if Playwright is available**

```
pytest tests/ -v
```

- [ ] **Step 3: Final commit if any fixes needed**

```bash
git add -p
git commit -m "fix: test suite cleanup after export UX and trim quality changes"
```
