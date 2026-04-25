# Export & Review Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four UX improvements: an Explorer folder button per clip on the Export page, sort-by-date in the Review tab, empty folder cleanup on clip delete, and a click-to-preview modal on the Export page.

**Architecture:** Backend changes go in `clipcutter/routes/encode.py` and `clipcutter/routes/review.py`. Frontend changes go in `clipcutter/static/src/api.ts`, `clipcutter/static/src/tabs/encode.ts`, and `clipcutter/static/src/main.ts`. All features are independent and can be committed separately.

**Tech Stack:** Python/FastAPI (backend), TypeScript/Vite (frontend), pytest/TestClient (API tests).

---

## Files Modified

- `clipcutter/routes/encode.py` — new `GET /api/open-folder/kept/{video_stem}` endpoint + empty folder cleanup in `delete_kept_clip`
- `clipcutter/routes/review.py` — include `processed_at` in clip dict, change sort key
- `clipcutter/static/src/api.ts` — add `openKeptFolder()` function
- `clipcutter/static/src/tabs/encode.ts` — add folder button + preview modal
- `clipcutter/static/src/main.ts` — register `openFolderHandler` and `previewClip` on `window._cc`
- `tests/test_export.py` — tests for open-folder endpoint and empty folder cleanup
- `tests/test_review.py` — test for review sort order

---

## Task 1: Open-Folder API Endpoint

**Files:**
- Modify: `clipcutter/routes/encode.py`
- Test: `tests/test_export.py`

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `tests/test_export.py`:

```python
import os
from unittest.mock import patch


class TestOpenFolder:
    def test_open_folder_not_found_returns_404(self, output_dir, app_client):
        resp = app_client.get("/api/open-folder/kept/no_such_stem")
        assert resp.status_code == 404

    def test_open_folder_calls_startfile(self, output_dir, app_client):
        stem = "openfolder"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/openfolder.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/openfolder.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep",
                        json={"segments": []})

        with patch("os.startfile") as mock_startfile:
            resp = app_client.get(f"/api/open-folder/kept/{stem}")

        assert resp.status_code == 200
        assert resp.json()["status"] == "opened"
        mock_startfile.assert_called_once()
        called_path = mock_startfile.call_args[0][0]
        assert stem in called_path
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_export.py::TestOpenFolder -v
```

Expected: FAIL — `404 Not Found` on the second test (endpoint doesn't exist yet).

- [ ] **Step 3: Add the endpoint to encode.py**

Open `clipcutter/routes/encode.py`. At the top, add `import os` to the imports (before `import threading`). Then add this route just before `return router` at the end of `create_router`:

```python
import os  # add at top of file

    @router.get("/api/open-folder/kept/{video_stem}")
    def open_folder(video_stem: str):
        folder = state.output_dir / DIR_CLIPS / DIR_KEPT / video_stem
        if not folder.exists():
            raise HTTPException(404, "Folder not found")
        os.startfile(str(folder))
        return {"status": "opened"}
```

- [ ] **Step 4: Run tests to confirm they pass**

```
pytest tests/test_export.py::TestOpenFolder -v
```

Expected: PASS both tests.

- [ ] **Step 5: Commit**

```bash
git add clipcutter/routes/encode.py tests/test_export.py
git commit -m "feat: add GET /api/open-folder/kept/{video_stem} endpoint"
```

---

## Task 2: Empty Folder Cleanup on Delete

**Files:**
- Modify: `clipcutter/routes/encode.py`
- Test: `tests/test_export.py`

- [ ] **Step 1: Write the failing test**

Add to `TestDeleteKeptClip` in `tests/test_export.py`:

```python
    def test_delete_removes_empty_folder(self, output_dir, app_client):
        stem = "emptyfoldervid"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/emptyfoldervid.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/emptyfoldervid.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep",
                        json={"segments": []})

        kept_dir = output_dir / "clips" / "kept" / stem
        assert kept_dir.exists()

        app_client.delete(f"/api/kept/{stem}/clip_001.mp4")

        assert not kept_dir.exists(), "Empty kept folder should be removed after last clip deleted"
```

- [ ] **Step 2: Run the test to confirm it fails**

```
pytest tests/test_export.py::TestDeleteKeptClip::test_delete_removes_empty_folder -v
```

Expected: FAIL — the folder still exists after delete.

- [ ] **Step 3: Add cleanup to delete_kept_clip in encode.py**

In `clipcutter/routes/encode.py`, find `delete_kept_clip`. After `kept_path.unlink()`, add:

```python
        kept_path.unlink()

        # Remove the parent folder if it's now empty
        if kept_path.parent.is_dir() and not any(kept_path.parent.iterdir()):
            kept_path.parent.rmdir()
```

- [ ] **Step 4: Run the tests to confirm they pass**

```
pytest tests/test_export.py::TestDeleteKeptClip -v
```

Expected: all `TestDeleteKeptClip` tests pass (including the new one).

- [ ] **Step 5: Commit**

```bash
git add clipcutter/routes/encode.py tests/test_export.py
git commit -m "fix: remove empty kept/{video_stem} folder after last clip deleted"
```

---

## Task 3: Review Sort by Date

**Files:**
- Modify: `clipcutter/routes/review.py`
- Test: `tests/test_review.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_review.py` (at the bottom, as a new class):

```python
class TestReviewSortOrder:
    """Clips sorted: newest-processed-video first, then by confidence within video."""

    def test_review_sorted_newest_video_first(self, output_dir, app_client):
        older_stem = "sort_older_review"
        newer_stem = "sort_newer_review"

        older_clip = create_pending_clip(
            output_dir, older_stem, "clip_001.mp4",
            source_video="/fake/older.mp4",
        )
        save_test_metadata(output_dir, older_stem, [older_clip], "/fake/older.mp4",
                           processed_at="2025-01-01T00:00:00")

        newer_clip = create_pending_clip(
            output_dir, newer_stem, "clip_001.mp4",
            source_video="/fake/newer.mp4",
        )
        save_test_metadata(output_dir, newer_stem, [newer_clip], "/fake/newer.mp4",
                           processed_at="2026-06-01T00:00:00")

        resp = app_client.get("/api/clips")
        assert resp.status_code == 200
        clips = resp.json()["clips"]
        test_clips = [c for c in clips
                      if c["video_stem"] in (older_stem, newer_stem)]
        assert len(test_clips) == 2
        assert test_clips[0]["video_stem"] == newer_stem, (
            "Newer-processed video clips should appear first"
        )
        assert test_clips[1]["video_stem"] == older_stem
```

- [ ] **Step 2: Run the test to confirm it fails**

```
pytest tests/test_review.py::TestReviewSortOrder -v
```

Expected: FAIL — clips are sorted by confidence only, not by date.

- [ ] **Step 3: Update list_clips in review.py**

Open `clipcutter/routes/review.py`. In `list_clips()`, find where `clips.append({...})` builds each clip dict. Add `"processed_at"` to that dict. It comes from `meta_data` which is already read above the loop.

The current append block ends around:

```python
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
```

Add `"processed_at": meta_data.get("processed_at", ""),` anywhere in that dict (e.g., after `"highlight_regions"`).

Then change the sort line from:

```python
        clips.sort(key=lambda c: -c["confidence"])
```

to:

```python
        clips.sort(key=lambda c: (c["processed_at"], c["confidence"]), reverse=True)
```

- [ ] **Step 4: Run the tests to confirm they pass**

```
pytest tests/test_review.py -v
```

Expected: all review tests pass including `TestReviewSortOrder`.

- [ ] **Step 5: Commit**

```bash
git add clipcutter/routes/review.py tests/test_review.py
git commit -m "fix: sort review clips by date processed (newest first) then confidence"
```

---

## Task 4: Frontend — Explore Button and Preview Modal

**Files:**
- Modify: `clipcutter/static/src/api.ts`
- Modify: `clipcutter/static/src/tabs/encode.ts`
- Modify: `clipcutter/static/src/main.ts`

No automated tests for these (no TS unit test framework). Verify manually by running the app.

- [ ] **Step 1: Add openKeptFolder to api.ts**

In `clipcutter/static/src/api.ts`, find the section of exported API helper functions (e.g. near `fetchKeptClips`). Add:

```typescript
export async function openKeptFolder(video_stem: string): Promise<void> {
  await apiGet<{ status: string }>(`/api/open-folder/kept/${encodeURIComponent(video_stem)}`);
}
```

- [ ] **Step 2: Import openKeptFolder in encode.ts**

At the top of `clipcutter/static/src/tabs/encode.ts`, the first import line is:

```typescript
import {
  fetchKeptClips, fetchPresets, startEncoding, fetchEncodeStatus, cancelEncoding,
  fetchYouTubeStatus, fetchYouTubePlaylists, startYouTubeAuth, revokeYouTubeAuth,
  startUpload, fetchUploadStatus, cancelUpload, createPlaylist, deleteKeptClip,
} from '../api';
```

Add `openKeptFolder` to that list:

```typescript
import {
  fetchKeptClips, fetchPresets, startEncoding, fetchEncodeStatus, cancelEncoding,
  fetchYouTubeStatus, fetchYouTubePlaylists, startYouTubeAuth, revokeYouTubeAuth,
  startUpload, fetchUploadStatus, cancelUpload, createPlaylist, deleteKeptClip,
  openKeptFolder,
} from '../api';
```

- [ ] **Step 3: Add openFolderHandler and previewClip functions to encode.ts**

Add both functions at the end of `clipcutter/static/src/tabs/encode.ts`, before the closing of the file:

```typescript
export async function openFolderHandler(video_stem: string): Promise<void> {
  try {
    await openKeptFolder(video_stem);
  } catch (e) {
    alert((e as Error).message);
  }
}

export function previewClip(index: number): void {
  const clip = keptClips[index];
  const url = clip.encoded_video_url || clip.video_url;

  const modal = document.createElement('div');
  modal.id = 'clipPreviewModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:1000;display:flex;align-items:center;justify-content:center';

  const video = document.createElement('video');
  video.src = url;
  video.controls = true;
  video.autoplay = true;
  video.style.cssText = 'max-width:90vw;max-height:85vh';

  modal.appendChild(video);
  document.body.appendChild(modal);

  const close = (): void => {
    video.pause();
    modal.remove();
    document.removeEventListener('keydown', onKey);
  };

  const onKey = (e: KeyboardEvent): void => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
}
```

- [ ] **Step 4: Update renderExportView to use the new functions**

In `renderExportView()` in `encode.ts`, find the clip row rendering loop (around line 85–98). Make two changes:

**Change 1** — make the clip name clickable (add `style="cursor:pointer"` and `onclick`):

Find:
```typescript
      html += `<span class="clip-name" title="${escapeHtml(clip.filename)}">${escapeHtml(clip.custom_name || clip.filename)}</span>`;
```

Replace with:
```typescript
      html += `<span class="clip-name" title="${escapeHtml(clip.filename)}" style="cursor:pointer" onclick="window._cc.previewClip(${i})">${escapeHtml(clip.custom_name || clip.filename)}</span>`;
```

**Change 2** — add the folder button after the existing delete button:

Find:
```typescript
      html += `<button class="btn-cancel" style="margin-left:auto;padding:2px 8px;font-size:12px" `
            + `data-stem="${escapeHtml(clip.video_stem)}" data-filename="${escapeHtml(clip.filename)}" `
            + `onclick="window._cc.deleteKeptClipHandler(this)">✕</button>`;
```

Replace with:
```typescript
      html += `<button class="btn-cancel" style="margin-left:auto;padding:2px 8px;font-size:12px" `
            + `data-stem="${escapeHtml(clip.video_stem)}" data-filename="${escapeHtml(clip.filename)}" `
            + `onclick="window._cc.deleteKeptClipHandler(this)">✕</button>`;
      html += `<button class="btn-secondary" style="padding:2px 8px;font-size:12px" `
            + `onclick="window._cc.openFolderHandler('${escapeHtml(clip.video_stem)}')" title="Open folder in Explorer">📁</button>`;
```

- [ ] **Step 5: Register new handlers in main.ts**

In `clipcutter/static/src/main.ts`, update the import from `./tabs/encode` to include `openFolderHandler` and `previewClip`:

Find:
```typescript
import { loadExportTab, renderExportView, toggleAllClips, startEncodingHandler, cancelEncodingHandler, startYouTubeAuthHandler, revokeYouTubeAuthHandler, startUploadHandler, cancelUploadHandler, keptClips, deleteKeptClipHandler } from './tabs/encode';
```

Replace with:
```typescript
import { loadExportTab, renderExportView, toggleAllClips, startEncodingHandler, cancelEncodingHandler, startYouTubeAuthHandler, revokeYouTubeAuthHandler, startUploadHandler, cancelUploadHandler, keptClips, deleteKeptClipHandler, openFolderHandler, previewClip } from './tabs/encode';
```

Then in the `handlers` object, add to the Encode section:

```typescript
  // Encode
  toggleAllClips,
  startEncodingHandler,
  cancelEncodingHandler,
  startYouTubeAuthHandler,
  revokeYouTubeAuthHandler,
  startUploadHandler,
  cancelUploadHandler,
  deleteKeptClipHandler,
  openFolderHandler,
  previewClip,
```

- [ ] **Step 6: Build and verify manually**

```bash
cd "E:/Projects/ClipCutter"
npm run build --prefix clipcutter/static
```

Expected: no TypeScript errors, build succeeds.

Then run the app and verify:
- Export page shows 📁 button on each clip row → clicking opens the `kept/{video_stem}/` folder in Explorer
- Clicking a clip name opens a dark modal with a video player → ESC or click outside closes it

- [ ] **Step 7: Commit**

```bash
git add clipcutter/static/src/api.ts clipcutter/static/src/tabs/encode.ts clipcutter/static/src/main.ts
git commit -m "feat: add folder explore button and clip preview modal to export page"
```

---

## Final Verification

- [ ] **Run full test suite**

```
pytest tests/ -v -k "not browser"
```

Expected: all non-browser tests pass.
