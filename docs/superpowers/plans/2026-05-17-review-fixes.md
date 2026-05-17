# Review-Fix Plan — 2026-05-17

> Source: deep code-and-test review on `main` 2026-05-17. Branch: `fix/review-2026-05-17`. Executed via superpowers:subagent-driven-development.

## Decisions resolved upfront

- **Re-process merge strategy (#6):** Merge — preserve `status`, `custom_name`, `encoded_filename`, `encoding_preset`, `youtube_video_id`, `youtube_url`, `youtube_upload_status`, `highlight_regions`, and `duration` (if user-trimmed) on any clip with a matching `filename`. Detection-derived fields (`confidence`, `detection_reasons`, `start_time`, `end_time`) come from the new detection. Clips that disappear from new detection stay (orphan-kept).
- **Compile cancel (#10):** Remove the endpoint and the frontend handler. Compilations are short enough that the half-built file + restart pattern is fine.
- **`serve_video` URL shape (#16):** Force canonical `/video/{kind}/{stem}/{file}` with `kind ∈ {pending, kept, encoded}`; compilations remain `/video/compilation/{file}`. No silent fallback. Frontend updated to use the precise kind it has.

## Task list

Tasks are listed in dependency order. Within a level, no order is required but the executor will go top-to-bottom.

### Wave 0 — Foundations (must land before everything else)

**Task 1: Line-ending hygiene.** Add `.gitattributes` with `* text=auto eol=lf` (and `*.bat text eol=crlf` for Windows-only files). Run `git add --renormalize .` and commit the renormalized tree. After this commit, no further file should appear "modified" purely because of CRLF.

**Task 2: Dependency manifest.** Add `[project.optional-dependencies]` to `pyproject.toml` with two groups: `youtube = ["google-api-python-client>=2.0", "google-auth>=2.0", "google-auth-oauthlib>=1.0"]` and `dev = ["pytest>=7", "httpx>=0.24", "playwright>=1.40", "pytest-playwright>=0.4"]`. Update CLAUDE.md install snippet to `pip install -e ".[youtube,dev]"`. Don't add the actual dep code — just declare the manifest correctly.

### Wave 1 — Independent fixes (no blockers, can run in any order)

**Task 3: Path traversal hardening (#3).** Add `_safe_join(base: Path, *parts: str) -> Path` to `routes/_helpers.py` that resolves the joined path and raises 400 (HTTPException) if `relative_to(base)` fails. Apply to every route that joins a path parameter into the filesystem: `serve_video`, `get_waveform`, `serve_encoded_video`, `delete_kept_clip`, `delete_encoded_clip`, `open_folder`, `delete_compilation`, `delete_compilation_sources`, `serve_compilation_video`, leftover-cleanup in `process.py`. Add tests for traversal rejection on at least one read and one delete endpoint.

**Task 4: Frontend XSS hardening (#4).** Rewrite `escapeHtml` in `static/src/utils.ts` to escape `& < > " '`. Audit every site that builds inline `onclick="fn('${escapeHtml(x)}')"` — convert them to `data-*` attributes plus delegated handlers using the pattern already used in `tabs/review.ts`. Particular attention to `tabs/compile.ts:225-229` (compilation_id + filename in inline JS), `tabs/encode.ts` (filename + custom_name + playlist title in attribute and inline-JS contexts).

**Task 5: Metadata write race (#5).** Add a `mutate_metadata(path: Path, mutator: Callable[[dict], None]) -> bool` helper in `clipcutter/metadata.py` that holds a `threading.Lock` keyed by the resolved path string (module-level `Dict[str, Lock]`), reads JSON, calls `mutator(data)`, and writes atomically via temp+rename. Refactor every `update_clip_*` to use it. Have each `update_clip_*` return `bool` indicating whether the matching filename was found; routes should 404 on miss (this resolves the related MEDIUM about silent no-ops).

**Task 7: `os.startfile` portability (#7).** Make `open_folder` cross-platform: on Windows use `os.startfile`, on Linux use `subprocess.Popen(["xdg-open", str(path)])`, on macOS use `subprocess.Popen(["open", str(path)])`. Mark the existing `test_open_folder_calls_startfile` with `@pytest.mark.skipif(not hasattr(os, "startfile"), reason="Windows-only")`; add a Linux/macOS equivalent that mocks `subprocess.Popen`. Tighten the assertion to verify the exact path passed.

**Task 8: Subprocess cancellation via Popen (#8).** Replace `subprocess.run` with `subprocess.Popen` in the workers under `routes/encode.py` (encode batch) and `routes/review.py` (`_do_keep` for trim-encode path). Store the active `Popen` on `state.enc.popen` and `state.keep.popen` (None when idle). Cancel routes call `.terminate()` (then `.kill()` after 5s if still alive). Convert `state.enc.cancelled` to `threading.Event` for proper memory-barrier semantics; do the same for the upload event.

**Task 9: YouTube upload cancellation + per-chunk timeout (#9).** In `clipcutter/youtube.py:upload_video`, pass `num_retries=` to `next_chunk()`, wrap the chunk loop with a per-chunk watchdog timeout, and check the cancellation event between chunks. Add an explicit `cancelled` return-value path so the route can distinguish cancel vs error.

**Task 10: Remove compile cancel endpoint (#10).** Delete `cancel_compilation` from `routes/compile.py`, delete `state.comp.cancelled` and any setter for it, remove the cancel button + handler from `tabs/compile.ts`, and drop any TS-side `cancelCompilation` API helper. Update the relevant test (if any) to reflect endpoint removal.

**Task 11: Subprocess and HTTP timeouts (#11).** Add `timeout=` to every `subprocess.run` in `clipcutter/audio.py`, `clipcutter/encoder.py`, `clipcutter/compiler.py`. Sensible ceilings: ffprobe 30s, audio extract `max(60, 3 * video_duration_s)`, clip extract 60s, encode `max(120, 10 * clip_duration_s)`, compile `max(300, 15 * total_input_s)`. Catch `subprocess.TimeoutExpired`, log, and re-raise as a domain exception that the caller can map to a 5xx with a useful message. Add `timeout=30` to the two `requests.post` calls in `clipcutter/youtube.py` (`exchange_code`, `refresh_access_token`).

**Task 12: OAuth correctness (#12).** In `clipcutter/youtube.py` / `routes/youtube.py`:
- Generate a random `state` parameter in `get_auth_url`; stash on `AppState`; verify on `/oauth/callback`; reject with 400 if mismatch or missing.
- `load_credentials` should return `None` when `refresh_token` is empty (currently returns a stub that fails later). The auth-start "partial creds" file should be removed; the OAuth flow shouldn't need an on-disk stub.
- After every `get_authenticated_service` call (and after each upload chunk's implicit refresh), if `creds.access_token` changed, persist via `save_credentials`. `routes/youtube.py:youtube_upload` worker must pick up the refreshed creds in the next clip iteration.
- `youtube_status` should catch `RefreshError`/`HttpError` specifically; map other exceptions to `{"authenticated": True, "error_class": ..., "error": str(...)}` rather than "expired credentials".
- `upload_video` HttpError handling: inspect `err.resp.status` and set `permanent: bool` on the result; the frontend can gate "retry" on `!permanent`.

**Task 13: `_split_long_clip` correctness (#13).** In `clipcutter/clipper.py`:
- After merging adjacent highlights in `compute_clip_boundaries`, filter each boundary's `highlights` list to only those whose `timestamp` lies within `[boundary.start_time, boundary.end_time]`. Use `list(b.highlights)` when extending to avoid sharing identity.
- In `_split_long_clip`, clamp `split_point` to `[clip.start_time + CLIP_MIN_LENGTH_SECONDS, clip.end_time - CLIP_MIN_LENGTH_SECONDS]` before recursion. Add a `depth` parameter with a sane max (e.g., 6) to prevent runaway recursion; on hitting the limit, fall back to even halving.
- Add tests with synthetic overlapping highlights that previously broke the splitter.

**Task 15: Web lifecycle (#15).** Three sub-changes:
- `clipcutter/web.py`: move `_cleanup_stale_pending` invocation from inline-in-`create_app` to a startup background thread so the server can accept requests immediately.
- `_cleanup_stale_pending` itself: also unlink any `.waveform.json` sidecars inside the per-stem directories (currently they block `rmdir`).
- `clipcutter/state.py`: replace `self.log_lines: List[str]` with `collections.deque(maxlen=500)`. Optional bonus: expose `since: int` query param on `/api/process/status` for incremental fetch.

**Task 16: Canonical video URL shape (#16).** Replace `/video/{video_stem}/{filename}` and `/video/encoded/{video_stem}/{filename}` with a single `/video/{kind}/{video_stem}/{filename}` where `kind ∈ {"pending", "kept", "encoded"}`. `/video/compilation/{filename}` remains as-is (no stem). Drop the silent fallback. Update every frontend call site (`tabs/review.ts`, `tabs/encode.ts`, `tabs/compile.ts`) to pass the precise kind. Update tests.

**Task 17: Misc MEDIUMs bundle (#17).** Tight, surgical fixes:
- `clipcutter/encoder.py`: in the GIF slowdown branch, append `-an` to the command (parity with the non-slowdown branch).
- `clipcutter/audio.py`: in `extract_clip`, inspect `result.stderr` for known-recoverable patterns before falling back to re-encode; otherwise raise with the captured tail. In the ffprobe call inside `extract_audio`, use `check=True`.
- `clipcutter/models.py`: `ClipMetadata.highlight_regions: List[dict] = field(default_factory=list)` (not `Optional[...] = None`); coerce `None → []` in `from_dict` for backward compat.
- `routes/compile.py:list_compilations`: tighten glob to `comp_????????_??????.json` (matches timestamp format) so videos named `comp_*` aren't picked up as compilations.
- `routes/encode.py:delete_kept_clip` and `routes/compile.py:delete_compilation`: return `leftover_files: list[str]` matching the pattern already used by `delete_source`.

**Task 18: Detection perf (#18).** Two hot-loop replacements:
- `clipcutter/features.py:compute_rolling_zscore`: replace the Python frame-loop with vectorized numpy using `np.cumsum` and broadcasting; result must be bit-identical (within float tolerance) on existing test fixtures.
- `clipcutter/detector.py`: replace the `np.array([np.median(padded[i:i+med_win]) for i in range(...)])` line with `scipy.ndimage.median_filter(onset, size=med_win, mode='reflect')`. Verify equivalence on a small fixture.

**Task 20: Playwright test quality (#20).** In `tests/test_ui_browser.py`:
- Replace every `page.wait_for_timeout(N)` (~13 sites) with `expect(locator).to_be_visible()` / `to_contain_text()` waits.
- Fix `test_keep_clip_via_button`: drop the "1 kept" optimistic-counter assertion (it's a tautology against frontend optimism); the existing `_wait_for_path` is the real check.
- Fix `test_chip_pct_moves_during_run`: assert "moved past 0 and reached >50%" rather than counting ≥3 distinct values in a fixed time window.

### Wave 2 — Blocked tasks (require Wave 1 tasks to land)

**Task 24: Re-process preserves user state (#6).** Blocked by Task 5 (so `save_metadata` can use the same lock pattern). Add `merge_with_existing` behavior to `save_metadata`: if an existing metadata file is present, for each new clip with a matching `filename`, preserve `status`, `custom_name`, `encoded_filename`, `encoding_preset`, `youtube_video_id`, `youtube_url`, `youtube_upload_status`, `highlight_regions`. Clips that exist in the old file but not the new detection result stay (orphan-kept). Use temp+rename for atomicity. Add a test that processes a fixture, marks clips as kept+encoded+uploaded in metadata, re-processes, and asserts those fields survived.

**Task 25: Frontend race / leak fixes (#14).** Blocked by Task 4 (XSS migration touches the same files). Three independent fixes:
- `loadExportTab` in `tabs/encode.ts`: capture an `inflightLoadToken = {}` at start, bail before assigning to `keptClips`/`storageSummary` if the token has been replaced. Apply the same pattern to `loadCompileTab` if it has the same shape.
- `main.ts:switchTab`: also `document.getElementById('clipPreviewModal')?.remove()` so the modal is torn down when the user navigates away.
- `tabs/encode.ts`: the YouTube auth `message` listener should be removed in a `finally`-equivalent path (success OR failure OR popup-closed). Use `popup.closed` polling to detect popup-without-auth, or a 5-minute timeout, then `removeEventListener`.

**Task 26: Browser tests use live server, not a second AppState (#21).** Blocked by Task 20 (related cleanup). In `tests/test_ui_browser.py`, replace the `TestClient(create_app(output_dir))` constructions inside `test_export_shows_kept_clips` and `test_preview_modal_uses_saved_volume` with HTTP requests against the live uvicorn server (`requests.post(f"{url}/api/clips/.../keep")` and polling `f"{url}/api/clips/keep/status"`).

**Task 27: Test coverage gaps (#19).** Blocked by Tasks 8 + 10 (cancel-route shape settles first). Add tests for:
- Cancel endpoints: `/api/encode/cancel`, `/api/youtube/upload/cancel` (compile cancel is gone after Task 10).
- 409 "already running" branches on `/api/process`, `/api/encode`, `/api/compilation`.
- `/api/clips/{stem}/{file}/keep` with a segment shorter than `CLIP_MIN_LENGTH_SECONDS` returns 400.
- Waveform 500 paths (ffmpeg failure, no audio, timeout).
- `/api/sources` LIST and `/api/sources/{stem}/delete` happy paths + 404.
- `youtube_status` when no credentials file exists returns `{authenticated: false}`.

## Out of scope

- Issue #11 on GitHub (path-traversal ticket) stays open as a record; the implementation lands on this branch.
- LOW/NIT-only items not included in this plan (original-preset+FPS labelling, terminal reviewer cleanup, dead `token_expiry`, `_session_video_dir`, assert-precedence trap).
