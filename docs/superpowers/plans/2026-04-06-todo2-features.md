# Todo2 Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add auto-clip of last minute, fix trimmed clip duration in Export, and improve Export UI with clip dates, date-sorted order, and per-clip delete.

**Architecture:** Three independent backend changes (clipper, metadata, encode routes) plus a frontend update. Each change is self-contained and tested before the next. Frontend is rebuilt with `npm run build` from `clipcutter/static/` at the end.

**Tech Stack:** Python 3.10+, FastAPI, pytest, TypeScript, Vite

---

## File Map

| File | Change |
|------|--------|
| `clipcutter/config.py` | Add `END_CLIP_DURATION_SECONDS`, `END_CLIP_TAIL_TOLERANCE_SECONDS` |
| `clipcutter/clipper.py` | Add `ensure_end_clip()` function |
| `clipcutter/pipeline.py` | Import and call `ensure_end_clip()` |
| `clipcutter/metadata.py` | Add `update_clip_duration()` |
| `clipcutter/routes/review.py` | Import `update_clip_duration`, call after trim |
| `clipcutter/routes/encode.py` | Add `clipped_at` to `/api/kept` response, change sort, add `DELETE /api/kept/{stem}/{filename}` |
| `tests/conftest.py` | Add `create_pending_clip_long()` helper, add `processed_at` param to `save_test_metadata` |
| `tests/test_clipper.py` | New — unit tests for `ensure_end_clip` |
| `tests/test_metadata.py` | Add `TestDurationUpdate` class |
| `tests/test_review.py` | Add `test_trim_updates_duration_in_metadata` |
| `tests/test_export.py` | Add `TestKeptClipsResponse`, `TestDeleteKeptClip` classes |
| `clipcutter/static/src/api.ts` | Add `clipped_at` to `KeptClipInfo`, add `deleteKeptClip()` |
| `clipcutter/static/src/tabs/encode.ts` | Show date, add delete button + handler |
| `clipcutter/static/src/main.ts` | Import and register `deleteKeptClipHandler` |

---

## Task 1: Config constants and `ensure_end_clip()`

**Files:**
- Modify: `clipcutter/config.py`
- Modify: `clipcutter/clipper.py`
- Create: `tests/test_clipper.py`

- [ ] **Step 1: Write failing tests for `ensure_end_clip`**

Create `tests/test_clipper.py`:

```python
"""Unit tests for clipper utility functions."""
from clipcutter.clipper import ensure_end_clip
from clipcutter.models import ClipBoundary, DetectionType


def _b(start: float, end: float) -> ClipBoundary:
    return ClipBoundary(start_time=start, end_time=end, highlights=[])


class TestEnsureEndClip:
    def test_adds_end_clip_when_uncovered(self):
        result = ensure_end_clip([_b(10.0, 50.0)], video_duration=600.0)
        assert len(result) == 2
        last = result[-1]
        assert last.end_time == 600.0
        assert last.start_time == 540.0  # 600 - 60

    def test_skips_when_boundary_reaches_end(self):
        result = ensure_end_clip([_b(500.0, 600.0)], video_duration=600.0)
        assert len(result) == 1

    def test_skips_within_tolerance(self):
        # Ends 3s before video end — within 5s tolerance
        result = ensure_end_clip([_b(500.0, 597.0)], video_duration=600.0)
        assert len(result) == 1

    def test_adds_when_just_outside_tolerance(self):
        # Ends 6s before video end — outside 5s tolerance
        result = ensure_end_clip([_b(500.0, 594.0)], video_duration=600.0)
        assert len(result) == 2

    def test_empty_boundaries_adds_clip(self):
        result = ensure_end_clip([], video_duration=600.0)
        assert len(result) == 1
        assert result[0].end_time == 600.0
        assert result[0].start_time == 540.0

    def test_short_video_clamped_to_start(self):
        # Video is 30s — shorter than END_CLIP_DURATION_SECONDS (60s)
        result = ensure_end_clip([], video_duration=30.0)
        assert len(result) == 1
        assert result[0].start_time == 0.0
        assert result[0].end_time == 30.0

    def test_added_clip_has_fallback_detection_type(self):
        result = ensure_end_clip([], video_duration=600.0)
        h = result[0].highlights[0]
        assert h.detection_type == DetectionType.FALLBACK

    def test_does_not_mutate_input_list(self):
        original = [_b(10.0, 50.0)]
        result = ensure_end_clip(original, video_duration=600.0)
        assert len(original) == 1  # input unchanged
        assert len(result) == 2
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_clipper.py -v
```

Expected: `ImportError: cannot import name 'ensure_end_clip' from 'clipcutter.clipper'`

- [ ] **Step 3: Add constants to `config.py`**

In `clipcutter/config.py`, after line 54 (`FALLBACK_DURATION_SECONDS = 300.0`):

```python
# End-of-recording clip
END_CLIP_DURATION_SECONDS = 60.0      # Always clip this many seconds from the end
END_CLIP_TAIL_TOLERANCE_SECONDS = 5.0  # If a clip ends within this many seconds of video end, skip
```

- [ ] **Step 4: Add `ensure_end_clip()` to `clipper.py`**

In `clipcutter/clipper.py`, add this import at the top of the file (it already imports `config` and `Highlight`, `DetectionType`, `ClipBoundary`):

The existing import line 12 is `from clipcutter import config` and line 11 imports from models. Confirm `DetectionType` and `Highlight` are already imported:

```python
from clipcutter.models import ClipBoundary, ClipMetadata, DetectionType, Highlight
```

After the `compute_fallback_clip` function (after line 142), add:

```python
def ensure_end_clip(boundaries: List[ClipBoundary],
                    video_duration: float) -> List[ClipBoundary]:
    """Add a last-N-seconds clip if no existing boundary reaches the end of the video.

    Only adds if every existing boundary ends more than END_CLIP_TAIL_TOLERANCE_SECONDS
    before the video end.
    """
    for b in boundaries:
        if b.end_time >= video_duration - config.END_CLIP_TAIL_TOLERANCE_SECONDS:
            return boundaries

    clip_len = min(config.END_CLIP_DURATION_SECONDS, video_duration)
    start = max(0.0, video_duration - clip_len)
    return boundaries + [ClipBoundary(
        start_time=start,
        end_time=video_duration,
        highlights=[Highlight(
            timestamp=start,
            duration=clip_len,
            detection_type=DetectionType.FALLBACK,
            raw_score=0.0,
            confidence=0.1,
        )],
    )]
```

- [ ] **Step 5: Run tests to confirm they pass**

```
pytest tests/test_clipper.py -v
```

Expected: 8 PASSED

- [ ] **Step 6: Commit**

```bash
git add clipcutter/config.py clipcutter/clipper.py tests/test_clipper.py
git commit -m "feat: add ensure_end_clip to always capture last minute of recording"
```

---

## Task 2: Wire `ensure_end_clip` into `pipeline.py`

**Files:**
- Modify: `clipcutter/pipeline.py`

- [ ] **Step 1: Update imports in `pipeline.py`**

In `clipcutter/pipeline.py`, the existing import block (lines 14–18):

```python
from clipcutter.clipper import (
    compute_clip_boundaries,
    compute_fallback_clip,
    extract_clips,
    format_duration,
    trim_silence,
)
```

Change to:

```python
from clipcutter.clipper import (
    compute_clip_boundaries,
    compute_fallback_clip,
    ensure_end_clip,
    extract_clips,
    format_duration,
    trim_silence,
)
```

- [ ] **Step 2: Call `ensure_end_clip` after boundary computation**

In `clipcutter/pipeline.py`, lines 97–103 currently read:

```python
        # Compute clip boundaries
        if highlights:
            boundaries = compute_clip_boundaries(highlights, video_duration)
        else:
            boundaries = compute_fallback_clip(video_duration)

        # Trim silence
        boundaries = [trim_silence(b, features) for b in boundaries]
```

Change to:

```python
        # Compute clip boundaries
        if highlights:
            boundaries = compute_clip_boundaries(highlights, video_duration)
        else:
            boundaries = compute_fallback_clip(video_duration)

        # Always include the last minute unless already covered
        boundaries = ensure_end_clip(boundaries, video_duration)

        # Trim silence
        boundaries = [trim_silence(b, features) for b in boundaries]
```

- [ ] **Step 3: Run the full test suite to confirm nothing broken**

```
pytest tests/ -v -k "not browser"
```

Expected: all existing tests pass

- [ ] **Step 4: Commit**

```bash
git add clipcutter/pipeline.py
git commit -m "feat: call ensure_end_clip in pipeline to always capture recording end"
```

---

## Task 3: `update_clip_duration()` in `metadata.py`

**Files:**
- Modify: `clipcutter/metadata.py`
- Modify: `tests/test_metadata.py`

- [ ] **Step 1: Write failing test**

In `tests/test_metadata.py`, add this import at the top alongside the existing imports:

```python
from clipcutter.metadata import (
    load_metadata,
    load_metadata_dict,
    save_metadata,
    update_clip_custom_name,
    update_clip_duration,
    update_clip_encoding,
    update_clip_status,
)
```

Then add this class at the end of `tests/test_metadata.py`:

```python
class TestDurationUpdate:
    """update_clip_duration persists the new duration correctly."""

    def test_update_duration(self, meta_dir):
        clips = [
            ClipMetadata(
                filename="clip_001.mp4",
                source_video="/videos/test.mp4",
                start_time=0.0, end_time=30.0, duration=30.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            ),
        ]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        update_clip_duration(meta_path, "clip_001.mp4", 20.0)

        loaded = load_metadata(meta_path)
        assert loaded[0].duration == 20.0

    def test_update_duration_in_raw_json(self, meta_dir):
        clips = [
            ClipMetadata(
                filename="clip_001.mp4",
                source_video="/videos/test.mp4",
                start_time=0.0, end_time=30.0, duration=30.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            ),
        ]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        update_clip_duration(meta_path, "clip_001.mp4", 15.5)

        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        assert raw["clips"][0]["duration"] == 15.5

    def test_update_duration_only_changes_target_clip(self, meta_dir):
        clips = [
            ClipMetadata(
                filename="clip_001.mp4",
                source_video="/videos/test.mp4",
                start_time=0.0, end_time=30.0, duration=30.0,
                detection_reasons=["volume_spike"], confidence=0.8,
            ),
            ClipMetadata(
                filename="clip_002.mp4",
                source_video="/videos/test.mp4",
                start_time=60.0, end_time=90.0, duration=30.0,
                detection_reasons=["laughter"], confidence=0.7,
            ),
        ]
        meta_path = save_metadata(clips, "/videos/test.mp4", meta_dir)

        update_clip_duration(meta_path, "clip_001.mp4", 20.0)

        loaded = load_metadata(meta_path)
        assert loaded[0].duration == 20.0
        assert loaded[1].duration == 30.0  # unchanged
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_metadata.py::TestDurationUpdate -v
```

Expected: `ImportError: cannot import name 'update_clip_duration'`

- [ ] **Step 3: Implement `update_clip_duration` in `metadata.py`**

In `clipcutter/metadata.py`, add this function at the end of the file (after `update_clip_youtube`):

```python
def update_clip_duration(meta_path: Path, filename: str, duration: float) -> None:
    """Update a single clip's duration in the metadata file."""
    data = json.loads(meta_path.read_text(encoding="utf-8"))

    for clip in data["clips"]:
        if clip["filename"] == filename:
            clip["duration"] = round(duration, 4)
            break

    tmp = meta_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(meta_path)
```

- [ ] **Step 4: Run tests to confirm they pass**

```
pytest tests/test_metadata.py -v
```

Expected: all tests PASSED (including the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add clipcutter/metadata.py tests/test_metadata.py
git commit -m "feat: add update_clip_duration to metadata module"
```

---

## Task 4: Fix trimmed clip duration in `review.py`

**Files:**
- Modify: `clipcutter/routes/review.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_review.py`

- [ ] **Step 1: Add `create_pending_clip_long` helper to `conftest.py`**

In `tests/conftest.py`, add this function after `create_pending_clip` (after line 142):

```python
def create_pending_clip_long(output_dir: Path, video_stem: str, filename: str,
                             source_video: str, file_duration_s: float = 3.0,
                             start: float = 0.0, end: float = 10.0,
                             confidence: float = 0.8) -> ClipMetadata:
    """Create a pending clip whose actual video file is file_duration_s long.

    Use this when a test needs to actually trim the clip (segments shorter than
    the full file duration), since _make_tiny_mp4 only creates 1-second files.
    """
    reasons = ["volume_spike"]
    clip_dir = output_dir / "clips" / "pending" / video_stem
    clip_dir.mkdir(parents=True, exist_ok=True)
    clip_path = clip_dir / filename

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
            "-f", "lavfi", "-i",
            f"color=c=black:s=160x120:d={file_duration_s}:r=10",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "32k",
            "-t", str(file_duration_s),
            str(clip_path),
        ],
        capture_output=True, text=True, check=True,
    )

    return ClipMetadata(
        filename=filename,
        source_video=source_video,
        start_time=start,
        end_time=end,
        duration=end - start,
        detection_reasons=reasons,
        confidence=confidence,
        status="pending",
    )
```

- [ ] **Step 2: Write failing test in `test_review.py`**

In `tests/test_review.py`, add this import at the top:

```python
import pytest
from tests.conftest import create_pending_clip, create_pending_clip_long, save_test_metadata
```

Then add this test inside the `TestTrimAndCustomName` class (after the existing `test_no_trim_when_no_segments` test):

```python
    def test_trim_updates_duration_in_metadata(self, output_dir, app_client):
        """When a clip is trimmed on keep, metadata duration reflects the trimmed length."""
        stem = "trimdur"
        clip = create_pending_clip_long(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/trimdur.mp4",
            file_duration_s=3.0, start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/trimdur.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={"segments": [{"start": 0.0, "end": 1.5}], "custom_name": None},
        )
        assert resp.status_code == 200
        assert resp.json()["trimmed"] is True

        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["duration"] == pytest.approx(1.5, abs=0.01)

    def test_full_clip_copy_does_not_change_duration(self, output_dir, app_client):
        """Keeping without trim should leave the metadata duration unchanged."""
        stem = "nodurchange"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/nodurchange.mp4",
            start=0.0, end=10.0,
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/nodurchange.mp4")

        resp = app_client.post(
            f"/api/clips/{stem}/clip_001.mp4/keep",
            json={"segments": [], "custom_name": None},
        )
        assert resp.status_code == 200
        assert resp.json()["trimmed"] is False

        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["duration"] == 10.0  # unchanged
```

- [ ] **Step 3: Run tests to confirm they fail**

```
pytest tests/test_review.py::TestTrimAndCustomName::test_trim_updates_duration_in_metadata -v
```

Expected: FAIL — `assert meta["clips"][0]["duration"] == pytest.approx(1.5, abs=0.01)` fails because duration is still `10.0`

- [ ] **Step 4: Update `review.py` to call `update_clip_duration` after trim**

In `clipcutter/routes/review.py`, line 14, change the metadata import from:

```python
from clipcutter.metadata import load_metadata, load_metadata_dict, update_clip_custom_name, update_clip_status
```

to:

```python
from clipcutter.metadata import load_metadata, load_metadata_dict, update_clip_custom_name, update_clip_duration, update_clip_status
```

Then in the `keep_clip` function, find the block after the if/elif/else (currently lines 259–263):

```python
        update_clip_status(meta_path, filename, "kept")

        if req and req.custom_name and req.custom_name.strip():
            update_clip_custom_name(meta_path, filename, req.custom_name.strip())

        return {"status": "kept", "trimmed": trimmed}
```

Change to:

```python
        update_clip_status(meta_path, filename, "kept")

        if trimmed:
            new_duration = sum(seg.end - seg.start for seg in segments)
            update_clip_duration(meta_path, filename, new_duration)

        if req and req.custom_name and req.custom_name.strip():
            update_clip_custom_name(meta_path, filename, req.custom_name.strip())

        return {"status": "kept", "trimmed": trimmed}
```

- [ ] **Step 5: Run tests to confirm they pass**

```
pytest tests/test_review.py -v
```

Expected: all tests PASSED

- [ ] **Step 6: Commit**

```bash
git add clipcutter/routes/review.py tests/conftest.py tests/test_review.py
git commit -m "fix: update metadata duration when clip is trimmed on keep"
```

---

## Task 5: `clipped_at` in `/api/kept` and date-sorted order

**Files:**
- Modify: `clipcutter/routes/encode.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_export.py`

- [ ] **Step 1: Add `processed_at` param to `save_test_metadata` in `conftest.py`**

In `tests/conftest.py`, find `save_test_metadata` (line 145) and change its signature and body from:

```python
def save_test_metadata(output_dir: Path, video_stem: str,
                       clips: list, source_video: str):
    """Write a metadata JSON matching what pipeline.py produces."""
    meta_dir = output_dir / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path = meta_dir / f"{video_stem}_clips.json"
    data = {
        "source_video": source_video,
        "processed_at": "2026-01-01T00:00:00",
        "clip_count": len(clips),
        "clips": [c.to_dict() for c in clips],
    }
```

to:

```python
def save_test_metadata(output_dir: Path, video_stem: str,
                       clips: list, source_video: str,
                       processed_at: str = "2026-01-01T00:00:00"):
    """Write a metadata JSON matching what pipeline.py produces."""
    meta_dir = output_dir / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path = meta_dir / f"{video_stem}_clips.json"
    data = {
        "source_video": source_video,
        "processed_at": processed_at,
        "clip_count": len(clips),
        "clips": [c.to_dict() for c in clips],
    }
```

- [ ] **Step 2: Write failing tests in `test_export.py`**

Add these classes at the end of `tests/test_export.py`:

```python
class TestKeptClipsResponse:
    """clipped_at field present in /api/kept response."""

    def test_clipped_at_included(self, output_dir, app_client):
        stem = "catvid"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/catvid.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/catvid.mp4",
                           processed_at="2026-03-15T10:00:00")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep",
                        json={"segments": []})

        resp = app_client.get("/api/kept")
        assert resp.status_code == 200
        clips = resp.json()["clips"]
        kept = next(c for c in clips if c["video_stem"] == stem)
        assert "clipped_at" in kept
        assert kept["clipped_at"] == "2026-03-15T10:00:00"

    def test_kept_clips_sorted_by_date_descending(self, output_dir, app_client):
        older_stem = "older_sort"
        newer_stem = "newer_sort"

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

        app_client.post(f"/api/clips/{older_stem}/clip_001.mp4/keep",
                        json={"segments": []})
        app_client.post(f"/api/clips/{newer_stem}/clip_001.mp4/keep",
                        json={"segments": []})

        resp = app_client.get("/api/kept")
        clips = resp.json()["clips"]
        test_clips = [c for c in clips
                      if c["video_stem"] in (older_stem, newer_stem)]
        assert len(test_clips) == 2
        assert test_clips[0]["video_stem"] == newer_stem
        assert test_clips[1]["video_stem"] == older_stem
```

Also add these imports at the top of `test_export.py` (existing imports already include `create_pending_clip, save_test_metadata` from conftest):

No new imports needed — `create_pending_clip` and `save_test_metadata` already imported.

- [ ] **Step 3: Run tests to confirm they fail**

```
pytest tests/test_export.py::TestKeptClipsResponse -v
```

Expected: FAIL — `KeyError: 'clipped_at'`

- [ ] **Step 4: Update `list_kept_clips` in `routes/encode.py`**

In `clipcutter/routes/encode.py`, find the `list_kept_clips` function (line 50). In the inner loop where `meta_data` is loaded, it already has:

```python
            meta_data = load_metadata_dict(meta_path)
            source_video = meta_data.get("source_video", video_stem)
```

Add `clipped_at` extraction right after:

```python
            meta_data = load_metadata_dict(meta_path)
            source_video = meta_data.get("source_video", video_stem)
            clipped_at = meta_data.get("processed_at", "")
```

Then in the `clip_info` dict (starting at line 79), add `"clipped_at": clipped_at` after `"video_url"`:

```python
                clip_info = {
                    "filename": clip.filename,
                    "source_video": source_video,
                    "video_stem": video_stem,
                    "start_time": clip.start_time,
                    "end_time": clip.end_time,
                    "duration": clip.duration,
                    "detection_reasons": clip.detection_reasons,
                    "confidence": clip.confidence,
                    "video_url": f"/video/{video_stem}/{clip.filename}",
                    "clipped_at": clipped_at,
                    "custom_name": clip.custom_name,
                    "encoded_filename": clip.encoded_filename,
                    "encoding_preset": clip.encoding_preset,
                    "youtube_video_id": clip.youtube_video_id,
                    "youtube_url": clip.youtube_url,
                    "youtube_upload_status": clip.youtube_upload_status,
                }
```

Then change the sort line (currently line 108):

```python
        clips.sort(key=lambda c: (-c["confidence"],))
```

to:

```python
        clips.sort(key=lambda c: c.get("clipped_at", ""), reverse=True)
```

- [ ] **Step 5: Run tests to confirm they pass**

```
pytest tests/test_export.py::TestKeptClipsResponse -v
```

Expected: 2 PASSED

- [ ] **Step 6: Run full suite**

```
pytest tests/ -v -k "not browser"
```

Expected: all tests PASSED

- [ ] **Step 7: Commit**

```bash
git add clipcutter/routes/encode.py tests/conftest.py tests/test_export.py
git commit -m "feat: add clipped_at to kept clips API and sort by date descending"
```

---

## Task 6: `DELETE /api/kept/{video_stem}/{filename}` endpoint

**Files:**
- Modify: `clipcutter/routes/encode.py`
- Modify: `tests/test_export.py`

- [ ] **Step 1: Write failing tests**

Add this class at the end of `tests/test_export.py`:

```python
class TestDeleteKeptClip:
    """DELETE /api/kept/{video_stem}/{filename} removes file and marks discarded."""

    def test_delete_removes_file(self, output_dir, app_client):
        stem = "delvid"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/delvid.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/delvid.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep",
                        json={"segments": []})

        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        assert kept_path.exists()

        resp = app_client.delete(f"/api/kept/{stem}/clip_001.mp4")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert not kept_path.exists()

    def test_delete_marks_metadata_discarded(self, output_dir, app_client):
        stem = "delmetavid"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/delmetavid.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/delmetavid.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep",
                        json={"segments": []})

        app_client.delete(f"/api/kept/{stem}/clip_001.mp4")

        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["status"] == "discarded"

    def test_delete_nonexistent_returns_404(self, output_dir, app_client):
        resp = app_client.delete("/api/kept/fakevid/nonexistent.mp4")
        assert resp.status_code == 404

    def test_deleted_clip_absent_from_kept_list(self, output_dir, app_client):
        stem = "delvid2"
        clip = create_pending_clip(output_dir, stem, "clip_001.mp4",
                                   source_video="/fake/delvid2.mp4")
        save_test_metadata(output_dir, stem, [clip], "/fake/delvid2.mp4")
        app_client.post(f"/api/clips/{stem}/clip_001.mp4/keep",
                        json={"segments": []})
        app_client.delete(f"/api/kept/{stem}/clip_001.mp4")

        resp = app_client.get("/api/kept")
        kept_for_stem = [c for c in resp.json()["clips"]
                         if c["video_stem"] == stem]
        assert len(kept_for_stem) == 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_export.py::TestDeleteKeptClip -v
```

Expected: FAIL — 405 Method Not Allowed (endpoint doesn't exist yet)

- [ ] **Step 3: Add DELETE endpoint to `routes/encode.py`**

In `clipcutter/routes/encode.py`, update the imports at line 10–11 to include `update_clip_status`:

```python
from clipcutter.metadata import load_metadata, load_metadata_dict, update_clip_encoding, update_clip_status
```

Then at the end of the `create_router` function (before `return router`), add:

```python
    @router.delete("/api/kept/{video_stem}/{filename}")
    def delete_kept_clip(video_stem: str, filename: str):
        """Delete a kept clip file and mark it as discarded in metadata."""
        kept_path = state.output_dir / DIR_CLIPS / DIR_KEPT / video_stem / filename
        meta_path = state.output_dir / DIR_METADATA / f"{video_stem}_clips.json"

        if not kept_path.exists():
            raise HTTPException(404, "Clip not found")

        kept_path.unlink()

        if meta_path.exists():
            update_clip_status(meta_path, filename, "discarded")

        return {"status": "deleted"}
```

- [ ] **Step 4: Run tests to confirm they pass**

```
pytest tests/test_export.py::TestDeleteKeptClip -v
```

Expected: 4 PASSED

- [ ] **Step 5: Run full suite**

```
pytest tests/ -v -k "not browser"
```

Expected: all tests PASSED

- [ ] **Step 6: Commit**

```bash
git add clipcutter/routes/encode.py tests/test_export.py
git commit -m "feat: add DELETE /api/kept endpoint to remove kept clips"
```

---

## Task 7: Frontend — date display, delete button, rebuild

**Files:**
- Modify: `clipcutter/static/src/api.ts`
- Modify: `clipcutter/static/src/tabs/encode.ts`
- Modify: `clipcutter/static/src/main.ts`

No automated tests for these — Vite build acts as the type-check gate.

- [ ] **Step 1: Add `clipped_at` to `KeptClipInfo` and `deleteKeptClip` in `api.ts`**

In `clipcutter/static/src/api.ts`, find the `KeptClipInfo` interface (line 22) and add `clipped_at` after `youtube_upload_status`:

```typescript
export interface KeptClipInfo {
  filename: string;
  source_video: string;
  video_stem: string;
  start_time: number;
  end_time: number;
  duration: number;
  detection_reasons: string[];
  confidence: number;
  video_url: string;
  custom_name: string | null;
  encoded_filename: string | null;
  encoding_preset: string | null;
  encoded_exists: boolean;
  encoded_video_url?: string;
  youtube_video_id: string | null;
  youtube_url: string | null;
  youtube_upload_status: string | null;
  clipped_at?: string;
}
```

Then add `deleteKeptClip` at the end of the `// ---- Encode ----` section (after `cancelEncoding`):

```typescript
export const deleteKeptClip = (videoStem: string, filename: string) =>
  apiDelete(`/api/kept/${encodeURIComponent(videoStem)}/${encodeURIComponent(filename)}`);
```

- [ ] **Step 2: Add `deleteKeptClipHandler` and update clip rows in `encode.ts`**

In `clipcutter/static/src/tabs/encode.ts`, add `deleteKeptClip` to the import at line 1:

```typescript
import {
  fetchKeptClips, fetchPresets, startEncoding, fetchEncodeStatus, cancelEncoding,
  fetchYouTubeStatus, fetchYouTubePlaylists, startYouTubeAuth, revokeYouTubeAuth,
  startUpload, fetchUploadStatus, cancelUpload, createPlaylist, deleteKeptClip,
} from '../api';
```

In `renderExportView()`, find the clip row rendering block (around line 77). The current row renders:

```typescript
      const dur = clip.duration ? Math.round(clip.duration) + 's' : '';
```

Add the date extraction right after:

```typescript
      const dur = clip.duration ? Math.round(clip.duration) + 's' : '';
      const date = clip.clipped_at ? clip.clipped_at.slice(0, 10) : '';
```

Then find the row HTML (around line 86–90):

```typescript
      html += `<div class="clip-row">`;
      html += `<input type="checkbox" class="clip-checkbox encode-cb" data-index="${i}" checked>`;
      html += `<span class="clip-name" title="${escapeHtml(clip.filename)}">${escapeHtml(clip.custom_name || clip.filename)}</span>`;
      html += `<span class="clip-detail">${escapeHtml(clip.video_stem || '')}</span>`;
      html += `<span class="clip-detail">${dur}</span>`;
      html += `<span class="tags" style="margin-bottom:0">${tags}</span>`;
      html += badge;
      html += `</div>`;
```

Change to:

```typescript
      html += `<div class="clip-row">`;
      html += `<input type="checkbox" class="clip-checkbox encode-cb" data-index="${i}" checked>`;
      html += `<span class="clip-name" title="${escapeHtml(clip.filename)}">${escapeHtml(clip.custom_name || clip.filename)}</span>`;
      html += `<span class="clip-detail">${escapeHtml(clip.video_stem || '')}</span>`;
      html += `<span class="clip-detail">${dur}</span>`;
      html += `<span class="clip-detail" style="color:#888">${date}</span>`;
      html += `<span class="tags" style="margin-bottom:0">${tags}</span>`;
      html += badge;
      html += `<button class="btn-cancel" style="margin-left:auto;padding:2px 8px;font-size:12px" `
            + `data-stem="${escapeHtml(clip.video_stem)}" data-filename="${escapeHtml(clip.filename)}" `
            + `onclick="window._cc.deleteKeptClipHandler(this)">✕</button>`;
      html += `</div>`;
```

Then add the `deleteKeptClipHandler` export at the end of `encode.ts` (after `cancelUploadHandler`):

```typescript
export async function deleteKeptClipHandler(btn: HTMLButtonElement): Promise<void> {
  const stem = btn.dataset.stem!;
  const filename = btn.dataset.filename!;
  if (!confirm(`Delete "${filename}"?`)) return;
  try {
    await deleteKeptClip(stem, filename);
    const row = btn.closest('.clip-row') as HTMLElement | null;
    if (row) row.remove();
    const idx = keptClips.findIndex(c => c.video_stem === stem && c.filename === filename);
    if (idx >= 0) keptClips.splice(idx, 1);
  } catch (e) {
    alert((e as Error).message);
  }
}
```

- [ ] **Step 3: Register `deleteKeptClipHandler` in `main.ts`**

In `clipcutter/static/src/main.ts`, update the import from `./tabs/encode` (line 3) to include `deleteKeptClipHandler`:

```typescript
import { loadExportTab, renderExportView, toggleAllClips, startEncodingHandler, cancelEncodingHandler, startYouTubeAuthHandler, revokeYouTubeAuthHandler, startUploadHandler, cancelUploadHandler, keptClips, deleteKeptClipHandler } from './tabs/encode';
```

Then add it to the `handlers` object (after `cancelUploadHandler`):

```typescript
  cancelUploadHandler,
  deleteKeptClipHandler,
  addSelectedToCompilation: () => addSelectedToCompilation(keptClips),
```

- [ ] **Step 4: Build the frontend**

```
cd clipcutter/static && npm run build
```

Expected: build succeeds with no TypeScript errors, `dist/` updated

- [ ] **Step 5: Commit**

```bash
git add clipcutter/static/src/api.ts clipcutter/static/src/tabs/encode.ts clipcutter/static/src/main.ts clipcutter/static/dist/
git commit -m "feat: show clip date and add delete button in Export tab"
```

---

## Self-Review

**Spec coverage:**
- ✅ Feature 1 (auto-clip last minute): Tasks 1–2
- ✅ Fix 2 (trimmed clip duration): Tasks 3–4
- ✅ Feature 3a (clip date): Task 5
- ✅ Feature 3b (sort by date descending): Task 5
- ✅ Feature 3c (delete button): Tasks 6–7

**Placeholder scan:** None found.

**Type consistency:**
- `ensure_end_clip` defined in Task 1, imported in Task 2 ✓
- `update_clip_duration` defined in Task 3, imported in Task 4 ✓
- `update_clip_status` already exists in `metadata.py`, used in Task 6 ✓
- `deleteKeptClip` added to `api.ts` in Task 7 step 1, used in `encode.ts` step 2 ✓
- `deleteKeptClipHandler` exported in `encode.ts` step 2, imported in `main.ts` step 3 ✓
- `clipped_at` added to `KeptClipInfo` in Task 7 step 1, accessed in `encode.ts` step 2 ✓
