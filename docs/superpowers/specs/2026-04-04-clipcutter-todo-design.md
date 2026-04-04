# ClipCutter Todo Implementation Design

**Date:** 2026-04-04  
**Scope:** All 6 items from todo.txt, executed in three ordered phases.

---

## Overview

Work is sequenced into three phases:

1. **Phase 1** — Quick bug fixes in the current codebase
2. **Phase 2** — Architecture refactor (backend split + Vite frontend)
3. **Phase 3** — New features built on the clean codebase

---

## Phase 1: Quick Fixes

### Fix #3 — Trim always re-encodes

**Root cause:** `web.py:599` checks `req.trim_end > 0.1` to determine if trimming is needed. But the frontend always sends the full clip duration as `trim_end` even when the user hasn't trimmed anything, so the condition is always true and FFmpeg always re-encodes.

**Fix:**
- Add `needs_trim: bool = False` to the `KeepRequest` dataclass in `web.py`
- In the frontend `keepClip()` function, set `needs_trim` using the existing JS logic: `trimStart > 0.1 || (clip.duration - trimEnd) > 0.1`
- On the server, replace the `req.trim_end > 0.1` check with `req.needs_trim`

### Fix #4 — Video keeps playing on save

**Fix:** In the frontend `keepClip()` function, call `video.pause()` before the `fetch()` call. One line.

### Fix #2 — Encode section shows raw filename instead of custom name

**Fix:** `index.html:1343` renders `clip.filename`. Change to `clip.custom_name || clip.filename`, keeping `clip.filename` as the `title` tooltip so the raw filename remains accessible on hover.

---

## Phase 2: Architecture Refactor

### Backend: Split `web.py` into routers

`web.py` becomes a thin app factory (~80 lines) that:
- Creates the FastAPI instance
- Registers 5 `APIRouter` modules from `clipcutter/routes/`
- Mounts static files
- Runs startup cleanup

**Router files (`clipcutter/routes/`):**

| File | Endpoints |
|------|-----------|
| `process.py` | `/api/process`, `/api/process/status`, `/api/process/cancel`, `/api/sources`, source deletion |
| `review.py` | `/api/clips` (list), `.../keep`, `.../discard`, `.../waveform`, `/video/pending/...` |
| `encode.py` | `/api/encode`, `/api/encode/status`, `/api/encode/cancel`, `/video/encoded/...` |
| `compile.py` | `/api/compilation`, `/api/compilation/status`, `/api/compilation/cancel`, `/video/compilation/...` |
| `youtube.py` | All `/api/youtube/...` and `/api/oauth/...` endpoints |

**Shared state (`clipcutter/state.py`):**

A small module imported by both routers and `web.py` that holds:
- `output_dir: Path` — resolved at startup, set once
- `encode_state: EncodeState` — background thread state
- `comp_state: CompilationState` — background thread state
- `process_state: ProcessState` — background thread state

This replaces the current closure-based approach where `output_dir` and state objects are captured in nested function definitions inside `create_app()`.

### Frontend: Vite + TypeScript

**Directory structure:**

```
clipcutter/static/
  src/
    main.ts          # App init, tab switching
    api.ts           # All fetch() wrappers with typed request/response shapes
    utils.ts         # Time formatting, escapeHtml, sanitize filename
    waveform.ts      # Canvas waveform rendering (extracted as-is from index.html)
    tabs/
      process.ts     # Process tab: folder input, progress log, start/cancel
      review.ts      # Review tab: clip loading, trim UI, segment list, keep/discard
      encode.ts      # Encode tab: clip list with custom names, preset selection, encode/cancel
      compile.ts     # Compile tab: drag-reorder, crossfade options, build/cancel
  index.html         # Vite entry point: same DOM structure and CSS, imports src/main.ts
  dist/              # Vite build output (gitignored)
  vite.config.ts     # outDir: dist, no framework plugins
  package.json       # devDependencies: vite, typescript only
```

FastAPI mounts `static/dist/` (built output) for production — one port, no CORS issues. During development, `vite dev` serves on port 5173 and FastAPI runs on port 8000. To avoid CORS issues in dev, `vite.config.ts` configures a proxy: `/api` and `/video` requests are forwarded to `http://localhost:8000`. No CORS middleware is needed on FastAPI.

**Key principles:**
- No runtime framework (no React, no Vue)
- TypeScript for type safety on request/response shapes and DOM interactions
- Each `tabs/*.ts` file handles one tab's complete logic, targeting ~200–350 lines each
- `api.ts` is the only place `fetch()` is called — all other modules import typed functions from it
- Existing CSS (dark theme, all class names) is preserved unchanged

**Build integration:**
- `package.json` dev script: `vite`
- `package.json` build script: `vite build`
- `ClipCutter.bat` gains a check: if `static/dist/` is missing or stale, run `npm run build` before starting uvicorn
- CI/test pipeline runs `npm run build` before `pytest`

**Existing tests:** API tests (TestClient) are unaffected — the API surface is identical. Playwright tests may need their selectors verified after the DOM restructure, but no structural changes to selectors are intended.

---

## Phase 3: New Features

### Feature #1 — Delete source clips after compilation

After a compilation completes successfully, the compilation card in the Export tab shows a "Clean up source clips" button.

**Endpoint:** `DELETE /api/compilation/{comp_id}/sources`
- Reads `comp_*.json` metadata to find the clip references used in the compilation
- Deletes their files from `clips/kept/<stem>/` and `clips/encoded/<stem>/` if they exist
- Updates each clip's metadata status to `"deleted"`
- Returns a list of deleted filenames

**UI:**
- Button is only shown when `compilation_status == "done"`
- Clicking shows a confirmation dialog listing the clip names (using `custom_name || filename`)
- On confirm, calls the endpoint and refreshes the export view
- On success, shows a count of files deleted

### Feature #5 — Multiple cut segments

Replaces the single In/Out trim pair in the review UI with a dynamic segment list.

**UI behaviour:**
- Default state: one segment spanning the full clip duration (identical to current behaviour)
- "Add segment" button appends a new In/Out pair below the list, defaulting to [clip_end - 10s, clip_end] (or the remaining unselected time)
- Each segment row has: In input, Set In button, Go In button, Out input, Set Out button, Go Out button, Remove button
- Segments are validated: each segment must be at least 1 second; segments must not overlap; they are sorted by start time before submission
- `I`/`O` keyboard shortcuts apply to whichever segment row is currently focused (first segment by default)
- Waveform shows all kept regions as green overlays; gaps between segments appear dimmed

**Data model changes:**

`KeepRequest` in `web.py`:
```python
@dataclass
class Segment:
    start: float
    end: float

@dataclass  
class KeepRequest:
    segments: list[Segment]       # replaces trim_start / trim_end / needs_trim
    custom_name: Optional[str] = None
```

Single-segment (full clip, no trim) is the degenerate case: one segment with `start=0, end=clip_duration`.

**Server-side FFmpeg logic (`routes/review.py`):**

- If one segment covering the full clip: `shutil.copy2` (no re-encode, same as current no-trim path)
- If one segment with trim: current single-pass `-ss / -t` FFmpeg command
- If multiple segments: use FFmpeg `concat` filter with `setpts=PTS-STARTPTS` reset per segment. No intermediate files — pipe all segments through a single filter graph.

```
ffmpeg -y \
  -ss {s0.start} -t {s0.end-s0.start} -i input.mp4 \
  -ss {s1.start} -t {s1.end-s1.start} -i input.mp4 \
  -filter_complex "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]" \
  -map "[v]" -map "[a]" \
  -c:v libx264 -c:a aac output.mp4
```

Each additional segment adds one more `-i input.mp4` and one more pair in the concat filter. This approach re-uses the same source file as multiple inputs, which FFmpeg handles correctly.

---

## Out of Scope

- No change to the CLI (`cli.py`, `reviewer.py`)
- No change to audio processing (`audio.py`, `features.py`, `detector.py`)
- No change to encoding presets (`encoder.py`, `config.py`)
- No change to the YouTube upload logic beyond moving it to its own router
- No new external Python dependencies
- No database — metadata remains JSON files

---

## Testing

- Phase 1 fixes: verify existing tests still pass; add a test that keeps a clip without trimming and asserts it is not re-encoded (check output file matches input by size/hash or verify FFmpeg was not called)
- Phase 2 refactor: all 34 existing tests must pass unchanged after the split
- Phase 3 features: add API tests for the new endpoints; add a Playwright test for the segment UI
