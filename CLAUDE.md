# ClipCutter

Audio-only video highlight extractor. Analyzes gaming videos (~10 min) for volume spikes, laughter, shouting, and sudden noises, then extracts clips for review.

## Quick Start

```bash
python -m clipcutter ui          # Web UI (Process + Review + Export tabs)
python -m clipcutter process .   # CLI processing
python -m clipcutter review      # CLI review
ClipCutter.bat                   # Double-click launcher (Windows)
```

## Stack

Python 3.10+, librosa (audio features), scipy/numpy (signal processing), click (CLI), FastAPI+uvicorn (web UI), FFmpeg (external), google-api-python-client + google-auth + google-auth-oauthlib (YouTube upload).

## Architecture

```
clipcutter/
  cli.py        # Click commands: process, review, ui
  pipeline.py   # Orchestrates: audio extract → features → detect → clip → metadata
  audio.py      # FFmpeg subprocess wrappers
  features.py   # Compute RMS, spectral centroid, onset strength, MFCCs in one pass
  detector.py   # 4 detectors (volume/laughter/shouting/noise) + scoring
  clipper.py    # Clip boundaries, merging, silence trim, extraction
  metadata.py   # JSON read/write/update
  models.py     # Dataclasses: Highlight, ClipBoundary, ClipMetadata
  config.py     # All tunable constants + encoding presets
  encoder.py    # FFmpeg encoding: presets (original/high/low/gif+slowdown)
  youtube.py    # YouTube Data API v3: OAuth2, upload, playlists
  reviewer.py   # Terminal-based keep/discard review
  web.py        # FastAPI app: 29 endpoints (process, review, encode, upload, OAuth)
  static/
    index.html  # SPA with Process + Review + Export tabs (dark theme)
```

## Output Structure

```
output/
  clips/pending/<video_stem>/   # Awaiting review
  clips/kept/<video_stem>/      # Approved clips (uncompressed)
  clips/encoded/<video_stem>/   # Encoded/re-encoded clips
  metadata/<video_stem>_clips.json  # Metadata with custom_name, encoding, YouTube status
  .youtube_credentials.json     # OAuth tokens (gitignored)
```

## Key Design Decisions

- **`-c copy` clip extraction**: Fast keyframe-aligned cuts, ~0.5s imprecision acceptable.
- **Config imports**: `clipper.py` uses `from clipcutter import config` (not `from config import X`) so CLI overrides (e.g. `--context`) propagate. Other modules use frozen imports (no runtime overrides needed).
- **Windows file locking**: `FileResponse` holds handles. Keep = `shutil.copy2`, discard = metadata-only. `_cleanup_stale_pending()` runs on startup.
- **Metadata `source_video`**: Stores full resolved path (not just filename) to support source deletion.
- **Custom names in metadata**: Review UI allows optional custom clip names (no file rename to avoid locking). Names stored in metadata, used during encoding/export for output filenames.
- **Encoding presets**: 4 options — `original` (copy, default), `high` (H.264 crf18), `low` (H.264 crf26), `gif` (animated GIF, no sound, optional slowdown via `setpts`). H.265/VP9 removed (codec availability issues on Windows).
- **GIF slowdown**: `slowdown_factor` param (0.25–1.0) only applies to GIF preset. Woven into FFmpeg palette filter chain.
- **YouTube OAuth**: Credentials stored in `output/.youtube_credentials.json` (dotfile, gitignored). Resumable chunked upload with progress tracking.

## Testing

32 tests: API tests (TestClient) + browser tests (Playwright/headless Chromium). Temp files cleaned up after each test. YouTube skipped (external dep).

```bash
pytest tests/ -v                    # Full suite (~42s)
pytest tests/ -v -k "not browser"   # API-only (~6s, no Playwright needed)
```

**Deps:** `pytest`, `httpx`, `playwright`, `pytest-playwright`. Setup: `python -m playwright install chromium`.

**Gotcha:** Silent video produces a fallback clip by design — tests assert fallback, not zero clips.
