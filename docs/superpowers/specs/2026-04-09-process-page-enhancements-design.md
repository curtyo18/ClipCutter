# Process Page Enhancements — Design Spec

**Date:** 2026-04-09

## Summary

Enhance the Process tab to show what videos are in the selected folder before processing, including file size, age, and processing status. Add a stale candidates section to surface old videos that haven't been dealt with, with per-file delete actions.

---

## Behaviour

### Scan trigger
- On page load, auto-scan the default folder (loaded via existing `GET /api/defaults`)
- A **Scan** button next to the Process button allows re-scanning after changing the folder path
- Both panels (Videos in Folder, Stale Candidates) are hidden until a scan has been run

### Videos in Folder panel
- Lists all video files (`.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`) found in the folder
- Columns: filename, size (GB/MB), age (days), status badge
- Status badges:
  - **processed** (green) — metadata exists, no pending clips
  - **pending review** (yellow) — metadata exists, has pending clips
  - **unprocessed** (red) — no metadata record
- Header line: `N videos · X.X GB`
- Hidden if folder is empty or scan hasn't run

### Stale Candidates panel
- Only rendered if at least one stale file exists
- Header line: `N files · X.X GB` + inline threshold input (default: 30 days, configurable)
- Threshold input re-filters client-side on change (no re-scan needed)
- Two row colors: red = unprocessed stale, amber = processed but source still present
- Each row has a **Delete** button — calls delete endpoint, then auto-re-scans
- Category column: `unprocessed` or `processed, kept`

---

## API

### `GET /api/folder-scan?folder=<path>&threshold_days=<n>`

Scans the directory for video files and cross-references against output metadata.

**Response:**
```json
{
  "videos": [
    {
      "filename": "session_2025-04-06.mp4",
      "size_mb": 2100.0,
      "age_days": 3,
      "status": "processed"
    }
  ],
  "total_size_mb": 4300.0,
  "stale": [
    {
      "filename": "session_2025-02-28.mp4",
      "size_mb": 700.0,
      "age_days": 40,
      "category": "unprocessed"
    }
  ],
  "stale_total_size_mb": 1800.0
}
```

**Status values:** `processed` | `pending_review` | `unprocessed`

**Category values:** `unprocessed` | `processed_kept`

**Status logic:**
- Check if `output/metadata/<stem>_clips.json` exists
- If not → `unprocessed`
- If yes and any clip has `status == "pending"` → `pending_review`
- If yes and no pending clips → `processed`

**Stale logic:**
- File `mtime` is older than `threshold_days` (default 30)
- Separated into two categories:
  - `unprocessed` — no metadata
  - `processed_kept` — metadata exists, all clips reviewed, source file still present

**Errors:** 400 if folder doesn't exist or isn't a directory.

---

### `DELETE /api/folder-scan/file`

Deletes a source video file from the folder.

**Body:**
```json
{ "folder": "C:\\Videos\\Gaming", "filename": "old_stream.mp4" }
```

**Guards:**
- 404 if file doesn't exist
- 400 if the file has a metadata record with pending clips (not fully reviewed)
- 400 if `folder/filename` resolves outside `folder` (path traversal guard)

**Response:**
```json
{ "status": "deleted", "freed_mb": 1100.0 }
```

---

## Frontend

### `process.ts` changes
- After loading defaults, call `scanFolder(defaultFolder)` automatically
- `scanFolder(folder)` — calls `GET /api/folder-scan`, renders both panels
- Scan button wired to `scanFolder(currentFolderValue)`
- Threshold input: on `input` event, re-render stale panel client-side from last scan result
- Delete button: calls `DELETE /api/folder-scan/file`, on success calls `scanFolder` again

### `index.html` changes
- Add **Scan** button next to Process button
- Add `#folderScanPanel` div between the form row and log box, initially hidden
- Inside: `#videosInFolderSection` and `#staleCandidatesSection`

### No new files
All changes go into existing `process.ts` and `index.html`.

---

## Error handling
- If scan fails (bad path, permission error), show a brief inline error below the folder input — do not crash the page
- If delete fails (pending clips guard), show an inline error on that row's delete button — do not remove the row

---

## Out of scope
- Bulk delete (select-all)
- Sorting/filtering the videos table
- Showing videos in subdirectories (recursive scan)
