# Event Delegation + API Test Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove inline `onclick` string handlers from `review.ts` using event delegation, and add missing API tests for multi-segment keep and delete-compilation-sources.

**Architecture:** Task 1 replaces all `onclick="window._cc.foo()"` strings with `data-action` attributes and a single delegated click listener. Tasks 2 and 3 add test classes to existing test files — no new files needed.

**Tech Stack:** TypeScript (Vite build), Python (pytest, FastAPI TestClient), FFmpeg (required for multi-segment keep test)

---

## File Map

| Action | Path |
|---|---|
| Modify | `clipcutter/static/src/tabs/review.ts` |
| Modify | `tests/test_compilation.py` |
| Modify | `tests/test_review.py` |

Tasks 1, 2, and 3 are fully independent and can be done in any order.

---

## Task 1: Event delegation in `review.ts`

**Files:**
- Modify: `clipcutter/static/src/tabs/review.ts`

### What's changing and why

Every inline `onclick="window._cc.someFunction(args)"` in HTML template strings is invisible to TypeScript — renaming a function won't produce a compile error. We replace them with `data-action` + `data-*` attributes and dispatch in one typed switch statement.

- `oninput` attributes on segment text inputs are left as-is (they are intentionally different — each needs `this.value` passed).
- `window._cc` and `window._savedVol` in `main.ts` are left as-is (they're typed and used for keyboard shortcuts and cross-tab calls).

- [ ] **Step 1: Add the module-level guard and `attachReviewListener()` to `review.ts`**

Add the following immediately after the `export let savedVolume = 0.5;` line (line 16):

```typescript
let reviewListenerAttached = false;

function attachReviewListener(): void {
  if (reviewListenerAttached) return;
  reviewListenerAttached = true;
  const container = document.getElementById('reviewContent');
  if (!container) return;
  container.addEventListener('click', (e: MouseEvent) => {
    const clicked = e.target as HTMLElement;
    if (clicked.tagName === 'INPUT') return;
    const target = clicked.closest('[data-action]') as HTMLElement | null;
    if (!target) return;
    const action = target.dataset.action!;
    const seg = target.dataset.seg !== undefined ? parseInt(target.dataset.seg, 10) : 0;
    const which = (target.dataset.which ?? 'in') as 'in' | 'out';
    const stem = target.dataset.stem ?? '';

    switch (action) {
      case 'keep':           clipAction('keep'); break;
      case 'skip':           clipAction('skip'); break;
      case 'discard':        clipAction('discard'); break;
      case 'add-segment':    addSegment(); break;
      case 'remove-segment': removeSegment(seg); break;
      case 'focus-segment':  focusSegment(seg); break;
      case 'set-point':      setSegmentPoint(seg, which); break;
      case 'seek-to':        seekToSegment(seg, which); break;
      case 'delete-source':  deleteSourceHandler(stem, target as HTMLButtonElement); break;
    }
  });
}
```

- [ ] **Step 2: Call `attachReviewListener()` at the top of `loadClips()`**

The current `loadClips()` starts at line 18. Add one line at the top of its body:

```typescript
export async function loadClips(): Promise<void> {
  attachReviewListener();
  const data = await fetchClips();
  // ... rest unchanged
```

- [ ] **Step 3: Remove inline onclick from the static initial segment row in `showClip()`**

In `showClip()`, the `segmentsHtml` template literal contains the first segment row (around lines 47–65). Replace the four trim buttons and the add-segment button:

**Before:**
```typescript
  const segmentsHtml = `
    <div class="trim-section" id="segmentList">
      <div class="segment-row" data-seg="0">
        <span class="trim-label">Seg 1</span>
        <span class="trim-label" style="margin-left:8px">In</span>
        <input type="text" class="trim-time seg-in" data-seg="0" value="0:00" />
        <button class="trim-btn" onclick="window._cc.setSegmentPoint(0,'in')">Set</button>
        <button class="trim-btn" onclick="window._cc.seekToSegment(0,'in')">Go</button>
        <span class="trim-label" style="margin-left:8px">Out</span>
        <input type="text" class="trim-time seg-out" data-seg="0" value="${fmtTimePrecise(clip.duration)}" />
        <button class="trim-btn" onclick="window._cc.setSegmentPoint(0,'out')">Set</button>
        <button class="trim-btn" onclick="window._cc.seekToSegment(0,'out')">Go</button>
      </div>
    </div>
    <div style="margin:6px 0">
      <button class="trim-btn" onclick="window._cc.addSegment()">+ Add segment</button>
      <span class="trim-indicator" id="trimIndicator"></span>
    </div>
  `;
```

**After:**
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
    <div style="margin:6px 0">
      <button class="trim-btn" data-action="add-segment">+ Add segment</button>
      <span class="trim-indicator" id="trimIndicator"></span>
    </div>
  `;
```

- [ ] **Step 4: Remove inline onclick from the action buttons in `showClip()`**

Still in `showClip()`, the `.actions` div (around lines 106–110):

**Before:**
```typescript
      <div class="actions">
        <button class="btn btn-keep" onclick="window._cc.clipAction('keep')">Keep <span class="shortcut">K</span></button>
        <button class="btn btn-skip" onclick="window._cc.clipAction('skip')">Skip <span class="shortcut">S</span></button>
        <button class="btn btn-discard" onclick="window._cc.clipAction('discard')">Discard <span class="shortcut">D</span></button>
      </div>
```

**After:**
```typescript
      <div class="actions">
        <button class="btn btn-keep" data-action="keep">Keep <span class="shortcut">K</span></button>
        <button class="btn btn-skip" data-action="skip">Skip <span class="shortcut">S</span></button>
        <button class="btn btn-discard" data-action="discard">Discard <span class="shortcut">D</span></button>
      </div>
```

- [ ] **Step 5: Remove inline onclick from `renderSegments()`**

The `renderSegments()` function builds HTML for each segment row. Replace the entire loop body:

**Before:**
```typescript
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    html += `<div class="segment-row" data-seg="${i}" onclick="window._cc.focusSegment(${i})" style="cursor:pointer;${i === activeSegmentIndex ? 'outline:1px solid #60a5fa;' : ''}">`;
    html += `<span class="trim-label">Seg ${i + 1}</span>`;
    html += `<span class="trim-label" style="margin-left:8px">In</span>`;
    html += `<input type="text" class="trim-time seg-in" data-seg="${i}" value="${fmtTimePrecise(seg.start)}" oninput="window._cc.onSegmentInput(${i},'in',this.value)" />`;
    html += `<button class="trim-btn" onclick="window._cc.setSegmentPoint(${i},'in')">Set</button>`;
    html += `<button class="trim-btn" onclick="window._cc.seekToSegment(${i},'in')">Go</button>`;
    html += `<span class="trim-label" style="margin-left:8px">Out</span>`;
    html += `<input type="text" class="trim-time seg-out" data-seg="${i}" value="${fmtTimePrecise(seg.end)}" oninput="window._cc.onSegmentInput(${i},'out',this.value)" />`;
    html += `<button class="trim-btn" onclick="window._cc.setSegmentPoint(${i},'out')">Set</button>`;
    html += `<button class="trim-btn" onclick="window._cc.seekToSegment(${i},'out')">Go</button>`;
    if (segments.length > 1) {
      html += `<button class="comp-remove" onclick="window._cc.removeSegment(${i})">&times;</button>`;
    }
    html += `</div>`;
  }
```

**After:**
```typescript
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    html += `<div class="segment-row" data-seg="${i}" data-action="focus-segment" style="cursor:pointer;${i === activeSegmentIndex ? 'outline:1px solid #60a5fa;' : ''}">`;
    html += `<span class="trim-label">Seg ${i + 1}</span>`;
    html += `<span class="trim-label" style="margin-left:8px">In</span>`;
    html += `<input type="text" class="trim-time seg-in" data-seg="${i}" value="${fmtTimePrecise(seg.start)}" oninput="window._cc.onSegmentInput(${i},'in',this.value)" />`;
    html += `<button class="trim-btn" data-action="set-point" data-seg="${i}" data-which="in">Set</button>`;
    html += `<button class="trim-btn" data-action="seek-to" data-seg="${i}" data-which="in">Go</button>`;
    html += `<span class="trim-label" style="margin-left:8px">Out</span>`;
    html += `<input type="text" class="trim-time seg-out" data-seg="${i}" value="${fmtTimePrecise(seg.end)}" oninput="window._cc.onSegmentInput(${i},'out',this.value)" />`;
    html += `<button class="trim-btn" data-action="set-point" data-seg="${i}" data-which="out">Set</button>`;
    html += `<button class="trim-btn" data-action="seek-to" data-seg="${i}" data-which="out">Go</button>`;
    if (segments.length > 1) {
      html += `<button class="comp-remove" data-action="remove-segment" data-seg="${i}">&times;</button>`;
    }
    html += `</div>`;
  }
```

- [ ] **Step 6: Remove inline onclick from `showDone()`**

In `showDone()`, the delete button (around line 311):

**Before:**
```typescript
            <button class="btn-delete" onclick="window._cc.deleteSourceHandler('${src.video_stem}', this)">Delete</button>
```

**After:**
```typescript
            <button class="btn-delete" data-action="delete-source" data-stem="${src.video_stem}">Delete</button>
```

- [ ] **Step 7: Build and verify TypeScript compiles**

```bash
cd clipcutter/static && npm run build
```

Expected: build completes with no errors, `dist/` is updated.

- [ ] **Step 8: Run browser tests to verify behavior is unchanged**

```bash
cd E:/Projects/ClipCutter && pytest tests/test_ui_browser.py -v
```

Expected: all browser tests pass (keep, discard, keyboard shortcuts, export tab).

- [ ] **Step 9: Commit**

```bash
git add clipcutter/static/src/tabs/review.ts clipcutter/static/dist/
git commit -m "refactor: replace inline onclick strings with event delegation in review.ts"
```

---

## Task 2: API tests for `DELETE /api/compilation/{id}/sources`

**Files:**
- Modify: `tests/test_compilation.py`

- [ ] **Step 1: Add `TestCompilationSources` class to `test_compilation.py`**

Add this class at the end of `tests/test_compilation.py` (after `TestCompilationDelete`):

```python
class TestCompilationSources:
    """DELETE /api/compilation/{id}/sources removes the source clip files."""

    def _write_compilation_meta(self, output_dir: Path, comp_id: str, clips: list) -> None:
        """Write a minimal compilation metadata JSON without running FFmpeg."""
        meta_dir = output_dir / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "compilation_id": comp_id,
            "filename": f"{comp_id}.mp4",
            "created_at": "2026-01-01T00:00:00",
            "clips": clips,
            "transition": "cut",
            "total_duration": 2.0,
            "status": "complete",
        }
        (meta_dir / f"{comp_id}.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def test_delete_sources_removes_kept_files(self, output_dir, app_client):
        stem = "delsrc"
        clips_data = []
        for i in range(1, 3):
            fname = f"clip_{i:03d}.mp4"
            clip = create_pending_clip(
                output_dir, stem, fname,
                source_video=f"/fake/{stem}.mp4",
            )
            clips_data.append(clip)
        save_test_metadata(output_dir, stem, clips_data, f"/fake/{stem}.mp4")

        for clip in clips_data:
            app_client.post(
                f"/api/clips/{stem}/{clip.filename}/keep",
                json={"segments": []},
            )

        # Both kept files should exist before we call delete
        for clip in clips_data:
            assert (output_dir / "clips" / "kept" / stem / clip.filename).exists()

        comp_id = "comp_srctest"
        self._write_compilation_meta(output_dir, comp_id, [
            {"video_stem": stem, "filename": clip.filename, "duration": 1.0}
            for clip in clips_data
        ])

        resp = app_client.delete(f"/api/compilation/{comp_id}/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_count"] == 2
        assert set(data["deleted"]) == {"clip_001.mp4", "clip_002.mp4"}

        # Kept files should be gone
        for clip in clips_data:
            assert not (output_dir / "clips" / "kept" / stem / clip.filename).exists()

        # Metadata should mark clips as deleted
        meta = json.loads(
            (output_dir / "metadata" / f"{stem}_clips.json").read_text(encoding="utf-8")
        )
        for c in meta["clips"]:
            assert c["status"] == "deleted", f"Expected deleted, got {c['status']}"

    def test_delete_sources_404_for_missing_compilation(self, output_dir, app_client):
        resp = app_client.delete("/api/compilation/comp_doesnotexist/sources")
        assert resp.status_code == 404
```

- [ ] **Step 2: Run the new tests to verify they pass**

```bash
pytest tests/test_compilation.py::TestCompilationSources -v
```

Expected output:
```
tests/test_compilation.py::TestCompilationSources::test_delete_sources_removes_kept_files PASSED
tests/test_compilation.py::TestCompilationSources::test_delete_sources_404_for_missing_compilation PASSED
```

- [ ] **Step 3: Run the full compilation test suite to confirm no regressions**

```bash
pytest tests/test_compilation.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_compilation.py
git commit -m "test: add API tests for DELETE compilation sources endpoint"
```

---

## Task 3: API test for multi-segment keep

**Files:**
- Modify: `tests/test_review.py`

**Note:** The existing `TestTrimAndCustomName` class already covers single-segment trim (`test_trim_and_custom_name`) and no-segments full-copy (`test_no_trim_when_no_segments`). This task adds only the missing multi-segment (concat) path.

- [ ] **Step 1: Add `TestMultiSegmentKeep` class to `test_review.py`**

Add this class at the end of `tests/test_review.py` (before the `# Helpers` section):

```python
class TestMultiSegmentKeep:
    """Keep with 2+ segments uses FFmpeg concat filter and reports combined duration."""

    def test_keep_with_multiple_segments(self, output_dir, app_client):
        stem = "multiseg"
        # 4-second clip so two 1.5s segments fit with a gap between them
        clip = create_pending_clip_long(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/multiseg.mp4",
            file_duration_s=4.0,
            start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/multiseg.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={
                "segments": [
                    {"start": 0.0, "end": 1.5},
                    {"start": 2.5, "end": 4.0},
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "kept"
        assert data["trimmed"] is True

        # File must exist (FFmpeg concat ran successfully)
        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()

        # Metadata duration should reflect combined segment length: 1.5 + 1.5 = 3.0s
        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["duration"] == pytest.approx(3.0, abs=0.1)
        assert meta["clips"][0]["status"] == "kept"
```

- [ ] **Step 2: Run the new test to verify it passes**

```bash
pytest tests/test_review.py::TestMultiSegmentKeep -v
```

Expected output:
```
tests/test_review.py::TestMultiSegmentKeep::test_keep_with_multiple_segments PASSED
```

- [ ] **Step 3: Run the full review test suite to confirm no regressions**

```bash
pytest tests/test_review.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_review.py
git commit -m "test: add multi-segment keep API test"
```

---

## Final verification

After all three tasks are complete:

```bash
pytest tests/ -v -k "not browser"
```

Expected: all API tests pass.

```bash
pytest tests/test_ui_browser.py -v
```

Expected: all browser tests pass (verifies event delegation did not break UI behavior).
