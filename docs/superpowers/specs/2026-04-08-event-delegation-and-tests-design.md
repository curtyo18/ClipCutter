# Event Delegation + Test Coverage Design

**Date:** 2026-04-08
**Scope:** Two medium-priority maintainability fixes — remove inline onclick strings from `review.ts`, add missing API test coverage for multi-segment keep and delete-compilation-sources.

---

## Problem

### 1. Inline `onclick` strings in `review.ts`

`showClip()` and `renderSegments()` build HTML strings containing `onclick="window._cc.someFunction(args)"`. These string-based handlers are invisible to TypeScript — renaming a function won't produce a type error, and argument types aren't checked. This is the same maintainability risk as the old monolithic `index.html`.

The `window._cc` global object in `main.ts` is fine and typed; only the inline string references are the problem.

### 2. Missing API test coverage

Two Phase 3 features have no tests:
- `DELETE /api/compilation/{id}/sources` endpoint
- Multi-segment keep (the `segments: []` field in `KeepRequest`)

---

## Solution

### 1. Event delegation in `review.ts`

Replace all inline `onclick` attributes with `data-action` + `data-*` attributes. Add one delegated `click` listener to `#reviewContent` (set up once in `loadClips()`). The listener reads `data-action` and dispatches fully-typed function calls.

**Action map:**

| `data-action` | Additional attrs | Dispatches |
|---|---|---|
| `"keep"` | — | `clipAction('keep')` |
| `"skip"` | — | `clipAction('skip')` |
| `"discard"` | — | `clipAction('discard')` |
| `"add-segment"` | — | `addSegment()` |
| `"remove-segment"` | `data-seg` | `removeSegment(idx)` |
| `"focus-segment"` | `data-seg` | `focusSegment(idx)` |
| `"set-point"` | `data-seg`, `data-which` | `setSegmentPoint(idx, which)` |
| `"seek-to"` | `data-seg`, `data-which` | `seekToSegment(idx, which)` |
| `"delete-source"` | `data-stem` | `deleteSourceHandler(stem, btn)` |

**What stays the same:**
- `window._cc` object in `main.ts` — kept (typed, used by keyboard shortcuts and cross-tab calls)
- `oninput` on segment text inputs — stays as-is; already delegated via `document.addEventListener('input', ...)` in `main.ts`
- All function signatures and logic — no behavior changes

**Affected functions in `review.ts`:**
- `showClip()` — static template for initial segment row + action buttons
- `renderSegments()` — dynamic template for all segment rows
- `showDone()` — delete source button

The listener is attached once inside `loadClips()` using a guard flag to prevent double-binding on repeated tab switches.

---

### 2. API tests

#### `test_compilation.py` — new class `TestCompilationSources`

- **`test_delete_sources_removes_kept_files`** — build a compilation from 2 kept clips; call `DELETE /api/compilation/{id}/sources`; assert both kept files no longer exist on disk; assert both clips have `status == "deleted"` in metadata.
- **`test_delete_sources_404_for_missing_compilation`** — call with a fake ID; assert 404.

Uses existing `_setup_kept_clips` helper and `_wait_for_compilation` — no new fixtures needed. No video required since the endpoint deletes by path, not FFmpeg.

#### `test_review.py` — new class `TestMultiSegmentKeep`

- **`test_keep_with_single_segment_trim`** — POST keep with `segments: [{start: 1.0, end: 3.0}]`; assert clip is kept; assert metadata duration updated to ~2.0s.
- **`test_keep_with_multiple_segments`** — POST keep with two segments; assert kept file is produced (FFmpeg concat ran); assert metadata duration reflects combined length.
- **`test_keep_full_clip_no_segments`** — POST keep with `segments: []`; assert treated as full clip (regression guard for existing behavior).

The multi-segment tests use the `mixed_video` fixture since FFmpeg must run to produce a real output file.

---

## Out of scope

- Browser tests for segment UI or delete-compilation-sources button (the API contracts are what need coverage; browser suite is already the slowest part of CI)
- Any changes to `window._cc` in `main.ts` itself
- Changes to other tab modules (`encode.ts`, `compile.ts`, `process.ts`)
