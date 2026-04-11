# Design: Export UX Improvements & Trim Quality

**Date:** 2026-04-11

## Overview

Five improvements targeting disk space visibility on the Export tab and clip quality when trimming in the Review tab.

---

## 1. Disk Usage Summary Bar

### What
A compact summary row at the top of the Export tab (above Encode Clips) showing storage consumed by each output category and a total.

```
Kept: 12 clips · 847 MB    Encoded: 8 clips · 1.2 GB    Compilations: 3 · 2.4 GB    Total: 4.5 GB
```

### API
- New `GET /api/storage-summary` endpoint in a suitable routes file (e.g. `routes/encode.py`).
- Backend walks `output/clips/kept/`, `output/clips/encoded/`, and `output/clips/compilations/`, summing file sizes and counting files.
- Returns:
  ```json
  {
    "kept": { "count": 12, "size_mb": 847.2 },
    "encoded": { "count": 8, "size_mb": 1228.8 },
    "compilations": { "count": 3, "size_mb": 2457.6 },
    "total_mb": 4533.6
  }
  ```

### Frontend
- `loadExportTab()` fetches `/api/storage-summary` in its `Promise.all` alongside other calls.
- Rendered as a muted single-line `<div>` above the Encode Clips section — no section heading, just a status line. Does not add visual weight.
- Refreshes automatically whenever the Export tab loads.

---

## 2. File Sizes in Clip Rows

### What
Each clip row in the Encode Clips list shows the kept file size. If an encoded version exists, shows both with an arrow:

```
clip_name.mp4  GameSession  23s  2026-04-09  [volume spike]  [Encoded]  kept: 184 MB → encoded: 42 MB  [✕] [📁]
```

### API
- Add `size_mb: float` and `encoded_size_mb: float | null` to each clip dict returned by `GET /api/kept`.
- Computed server-side: `size_mb` from `kept/{stem}/{filename}`, `encoded_size_mb` from `encoded/{stem}/{encoded_filename}` when `encoded_exists` is true.
- Add corresponding optional fields to the `KeptClipInfo` TypeScript interface in `api.ts`.

### Frontend
- In `encode.ts:renderExportView()`, add the size info as a `clip-detail` span after the encoded badge.
- Only show `encoded_size_mb` when `encoded_exists` is true. Show `→ Y MB` portion only when encoded version exists.

---

## 3. Trim Quality Fix

### What
Two changes to `review.py:keep_clip()` to eliminate quality loss during review trimming.

### Single-segment trim
Switch from re-encode to stream copy:

```python
# Before
["ffmpeg", "-y", "-ss", f"{seg.start:.3f}", "-i", str(clip_path),
 "-t", f"{duration:.3f}", "-c:v", "libx264", "-c:a", "aac", str(dest)]

# After
["ffmpeg", "-y", "-ss", f"{seg.start:.3f}", "-i", str(clip_path),
 "-t", f"{duration:.3f}", "-c", "copy", "-avoid_negative_ts", "make_zero", str(dest)]
```

Zero quality loss. Same ~0.5–1s keyframe imprecision already accepted throughout the app.

### Multi-segment keep — three quality modes

Add optional `quality` field to `KeepRequest`:

```python
class KeepRequest(BaseModel):
    segments: list[Segment] = []
    custom_name: Optional[str] = None
    quality: str = "copy"  # "copy" | "precise" | "ultra"
```

| Mode | FFmpeg flags | Quality | Cut precision | File size |
|---|---|---|---|---|
| `copy` (default) | Two-pass `-c copy` + concat demuxer | Identical to source | ~0.5–1s off per cut | Same as source |
| `precise` | `libx264 -crf 16 -c:a aac` | Visually identical | Frame-exact | ~2–3x larger |
| `ultra` | `libx264 -crf 0 -c:a aac` | True lossless | Frame-exact | ~10–50x larger |

**Two-pass copy implementation:**
1. Create a temp directory via `tempfile.mkdtemp()`, cleaned up in a `finally` block.
2. For each segment: `ffmpeg -ss {start} -t {dur} -i clip.mp4 -c copy temp_dir/seg_N.mp4`
3. Write a concat filelist (`file 'seg_0.mp4'\nfile 'seg_1.mp4'\n...`) into the temp dir.
4. `ffmpeg -f concat -safe 0 -i filelist.txt -c copy output.mp4`
5. `finally`: remove the temp directory and all segment files.

### Frontend
- Add a `<select>` with three options to the trim section in the Review tab, always visible alongside the trim controls:
  ```
  Trim quality: [Fast (copy) ▾]
  Options: Fast (copy) | Precise (CRF 16) | Ultra (lossless)
  ```
- Pass selected value as `quality` in the `KeepRequest` body.
- Add `quality?: string` to the `KeepRequest` TypeScript interface in `api.ts`.
- Backend ignores `quality` for single-segment and full-clip keeps (always uses copy). Quality mode only takes effect for multi-segment keeps.

---

## 4. Delete Encoded Version

### What
When a clip has an encoded version (shows the "Encoded" badge), a "✕ encoded" button appears next to the badge. Clicking it deletes only the encoded file — the original kept clip is untouched, allowing re-encode with different settings or freeing disk space.

### API
- New `DELETE /api/encoded/{video_stem}/{filename}` endpoint in `routes/encode.py`.
- Deletes the file at `encoded/{stem}/{encoded_filename}` (looked up via metadata).
- Clears `encoded_filename`, `encoding_preset`, `encoded_exists` fields in metadata JSON.
- Returns `{"status": "deleted", "freed_mb": X}`.

### Frontend
- In `encode.ts:renderExportView()`, render a small "✕ encoded" button next to the Encoded badge when `encoded_exists` is true.
- On success: remove badge and button from the row DOM, update `keptClips[i]` in memory (`encoded_exists = false`, `encoded_filename = null`).
- Add `deleteEncodedClip(videoStem, filename)` to `api.ts`.

---

## 5. Persistent Source Video Cleanup

### What
The source video cleanup list currently only appears in the review "done" state and disappears after navigation. This adds it as a permanent "Source Videos" section at the bottom of the Export tab.

### API
Uses existing endpoints — no new backend work:
- `GET /api/sources` — returns `source_path`, `exists`, `size_mb`, `fully_reviewed`, `kept`, `discarded`, `total` per source video
- `POST /api/sources/{stem}/delete` — deletes the source file

### Frontend
- New "Source Videos" `export-section` rendered at the bottom of `renderExportView()`, after the YouTube section.
- Only rendered when at least one source with `exists: true` is returned.
- Each row shows: filename (truncated), size in MB, review summary (e.g. "12 kept / 3 discarded / fully reviewed" or "8 kept / 2 discarded / 2 pending"), and a Delete button.
- `fully_reviewed: true` rows style the review summary in muted green; incomplete rows in yellow.
- Delete button calls existing `deleteSource(stem)` — on success, removes the row from the DOM.
- `loadExportTab()` fetches `/api/sources` in its `Promise.all`.

---

## Scope

### Files changed
- `clipcutter/routes/encode.py` — storage summary endpoint, delete encoded endpoint, size fields in `/api/kept`
- `clipcutter/routes/review.py` — trim quality fix (single-segment copy, multi-segment quality modes)
- `clipcutter/static/src/api.ts` — `StorageSummary` type, `deleteEncodedClip()`, `fetchStorageSummary()`, `quality` on `KeepRequest`, `size_mb`/`encoded_size_mb` on `KeptClipInfo`
- `clipcutter/static/src/tabs/encode.ts` — summary bar, size columns, delete encoded button, source videos section
- `clipcutter/static/src/tabs/review.ts` — quality selector in trim controls

### No new files, no new dependencies, no database migrations.
