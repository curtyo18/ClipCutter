# Design: Export & Review Improvements

**Date:** 2026-04-10

## Overview

Four small improvements to the Export and Review tabs, addressed in priority order.

---

## 1. Explore Button on Export Page (Task 2)

### What
A folder-open button per clip row on the Export page's Encode Clips list. Clicking it opens the clip's `kept/{video_stem}/` folder in Windows Explorer.

### API
- `GET /api/open-folder/kept/{video_stem}` — added to `encode.py` router.
- Backend constructs the full path from `state.output_dir / DIR_CLIPS / DIR_KEPT / video_stem`, validates it exists, then calls `os.startfile(str(path))`.
- Returns `{"status": "opened"}` on success, 404 if the folder doesn't exist.

### Frontend
- In `encode.ts:renderExportView()`, add a 📁 icon button at the end of each clip row in the Encode Clips list.
- `onclick` calls `fetch('/api/open-folder/kept/${video_stem}')` — fire and forget, no UI state change.
- New `api.ts` function: `openKeptFolder(video_stem: string)`.

---

## 2. Review Sort by Date (Task 3)

### What
Change clip ordering in the Review tab from confidence-only to: newest processed video first, then within each video by highest confidence.

### Change
In `review.py:list_clips()`:
- Include `"processed_at": meta_data.get("processed_at", "")` in each clip dict.
- Change sort from `clips.sort(key=lambda c: -c["confidence"])` to `clips.sort(key=lambda c: (c["processed_at"], c["confidence"]), reverse=True)`.
- ISO 8601 strings sort lexicographically, so `reverse=True` gives newest first.

No frontend changes required.

---

## 3. Empty Folder Cleanup on Delete (Task 1)

### What
When the last kept clip in a `kept/{video_stem}/` folder is deleted, remove the now-empty directory.

### Change
In `encode.py:delete_kept_clip()`, after `kept_path.unlink()`:
```python
if kept_path.parent.is_dir() and not any(kept_path.parent.iterdir()):
    kept_path.parent.rmdir()
```

No API or frontend changes.

---

## 4. Clip Preview Modal on Export Page (Task 4)

### What
Clicking a clip's name on the Export page opens a modal video player for quick review.

### Frontend
- In `encode.ts:renderExportView()`, wrap the clip name `<span>` in a clickable element with `onclick="window._cc.previewClip(${i})"`.
- Add `previewClip(index: number)` to `encode.ts`: creates a modal overlay (dark semi-transparent backdrop), centered `<video controls autoplay>` using `encoded_video_url` if available, else `video_url`.
- Modal closes on: click outside the video, or `Escape` key.
- Modal is created dynamically (no persistent DOM element), removed on close.
- No new API endpoint needed.

### Modal structure
```html
<div id="clipPreviewModal" style="position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:1000;display:flex;align-items:center;justify-content:center">
  <video src="..." controls autoplay style="max-width:90vw;max-height:85vh"></video>
</div>
```

---

## Scope

- No database migrations, no new files, no new deps.
- All changes are in: `clipcutter/routes/encode.py`, `clipcutter/routes/review.py`, `clipcutter/static/src/tabs/encode.ts`, `clipcutter/static/src/api.ts`.
