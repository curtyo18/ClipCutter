# ClipCutter todo2 Features Design

**Date:** 2026-04-06

## Overview

Three changes: always clip the last minute of a recording, fix trimmed clip duration in the Export tab, and improve the Export tab UI with dates, date-sorted order, and per-clip delete.

---

## Feature 1: Auto-clip last minute of video

### Goal
Gaming recordings are intentional — there's usually something worth keeping at the end. Always clip the final minute unless the detection pipeline already covers the end of the video.

### Design

Add `ensure_end_clip(boundaries, video_duration, clip_len=60.0)` to `clipper.py`.

**Logic:**
- If any existing boundary ends within 5s of `video_duration`, return `boundaries` unchanged.
- Otherwise, append a new `ClipBoundary(start=max(0, video_duration - clip_len), end=video_duration)` with a single `FALLBACK` highlight (confidence=0.1).

**Integration in `pipeline.py`:**
```python
boundaries = compute_clip_boundaries(highlights, video_duration)
if not boundaries:
    boundaries = compute_fallback_clip(video_duration)
boundaries = ensure_end_clip(boundaries, video_duration)  # NEW
boundaries = [trim_silence(b, features) for b in boundaries]
```

**Constants in `config.py`:**
- `END_CLIP_DURATION_SECONDS = 60.0` — how long to clip from the end
- `END_CLIP_TAIL_TOLERANCE_SECONDS = 5.0` — how close to video end counts as "covered"

---

## Fix 2: Trimmed clip duration in Export

### Goal
When a clip is kept with trim segments in the Review tab, the Export tab still shows the original (pre-trim) duration. Fix it to show the actual kept duration.

### Root cause
`keep_clip` in `routes/review.py` re-encodes when `segments` are given but never updates `duration` in the metadata JSON. The Export tab reads `clip.duration` directly from metadata.

### Design

Add to `metadata.py`:
```python
def update_clip_duration(meta_path: Path, filename: str, duration: float) -> None:
    """Update a single clip's duration in the metadata file."""
```

In `keep_clip` (`routes/review.py`), after a successful trim:
```python
if trimmed:
    new_duration = sum(seg.end - seg.start for seg in segments)
    update_clip_duration(meta_path, filename, new_duration)
```

No changes to `start_time`/`end_time` — those still reflect the original source video timeline, which is used for YouTube description templates. Only `duration` is corrected.

---

## Feature 3: Export tab UI improvements

### Goal
The clip list in Export needs the date it was clipped, sorted newest-first, and a way to delete clips you no longer want.

### Design

**Backend — `/api/kept` response:**
- Read `processed_at` from the metadata file's top level and include it as `clipped_at` per clip in the response.
- Change sort in `list_kept_clips()` from `(-confidence,)` to `clipped_at` descending (ISO string, lexicographic sort works).

**Backend — new delete endpoint:**
```
DELETE /api/kept/{video_stem}/{filename}
```
- Removes the file from `clips/kept/{video_stem}/`.
- Marks clip status as `"discarded"` in metadata (reuses `update_clip_status`).
- Returns `{"status": "deleted"}`.
- Lives in `routes/encode.py` alongside the other kept-clip endpoints.

**Frontend — `encode.ts`:**
- Display `clipped_at` date (formatted as `YYYY-MM-DD`) in the clip row.
- Add a delete button per clip row that calls the delete endpoint and removes the row from the DOM (no full reload needed).
- The `KeptClipInfo` type in `api.ts` gets a `clipped_at?: string` field.

---

## Files changed

| File | Change |
|------|--------|
| `clipcutter/config.py` | Add `END_CLIP_DURATION_SECONDS`, `END_CLIP_TAIL_TOLERANCE_SECONDS` |
| `clipcutter/clipper.py` | Add `ensure_end_clip()` |
| `clipcutter/pipeline.py` | Call `ensure_end_clip()` after boundary computation |
| `clipcutter/metadata.py` | Add `update_clip_duration()` |
| `clipcutter/routes/review.py` | Call `update_clip_duration()` after trim |
| `clipcutter/routes/encode.py` | Include `clipped_at` in `/api/kept`, change sort, add DELETE endpoint |
| `clipcutter/static/src/api.ts` | Add `clipped_at` to `KeptClipInfo`, add `deleteKeptClip()` |
| `clipcutter/static/src/tabs/encode.ts` | Show date, add delete button per row |
