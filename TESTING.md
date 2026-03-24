# ClipCutter Integration Testing Plan

**Scope**: UI workflows end-to-end. Skip YouTube (external dependency). Focus on clip detection → review → encoding → export.

## Test Approach

Use **pytest** + **Selenium/Playwright** for browser automation. Keep tests focused on critical workflows, not code coverage.

## Test Data

Create minimal synthetic test videos in `tests/fixtures/videos/`:

1. **silence_5s.mp4** (5 sec, silent) — should produce no highlights
2. **noise_10s.mp4** (10 sec, white noise) — should trigger sudden_noise detector
3. **mixed_10s.mp4** (10 sec: 3s silence + 4s noise + 3s silence) — realistic workflow test

Tools: FFmpeg CLI to generate. Store as committed binary files (or generate on-the-fly via fixture).

## Test Scenarios

### 1. Full Pipeline (Process → Review → Keep → Export)
- **Input**: mixed_10s.mp4
- **Actions**:
  - Process video (default sensitivity)
  - Verify clips are created and listed in Review tab
  - Keep 1 clip (with custom name "test_clip")
  - Verify custom_name is stored in metadata
  - Verify clip appears in Export tab's kept list
  - Encode to "copy" preset
  - Verify encoded file exists in `output/clips/encoded/`
- **Assertions**: Metadata has custom_name, encoded file present, file sizes match expectations

### 2. Encoding Presets
- **Input**: 1 kept clip
- **Actions**:
  - Encode to each preset (copy, h264_hq, h265, vp9_webm)
  - Verify output extensions match preset
- **Assertions**: All 4 encoded files exist with correct extensions

### 3. Custom Name in Export
- **Input**: Clip with custom_name="My Great Moment"
- **Actions**:
  - Open Export tab
  - Verify upload title field defaults to custom_name
  - Encode: verify output filename uses sanitized custom_name
- **Assertions**: Output file uses "My_Great_Moment" prefix (spaces→underscores)

### 4. Trim & Custom Name
- **Input**: mixed_10s.mp4
- **Actions**:
  - Process → Review
  - Keep clip with trim (trim_start=2, trim_end=8) AND custom_name="Trimmed"
  - Encode with copy preset
- **Assertions**: Trimmed file in kept/, encoded file has custom_name in output filename

### 5. Discard & Cleanup
- **Input**: 3 clips detected
- **Actions**:
  - Keep 1, discard 2
  - Verify pending dir only contains kept clip
- **Assertions**: Discarded clips don't linger on disk

## Test Structure

```
tests/
  conftest.py              # Fixtures: app, browser, temp output dir
  fixtures/
    videos/                # Test video files (or generate on-the-fly)
      mixed_10s.mp4
      silence_5s.mp4
      noise_10s.mp4
    expected/              # Sample metadata/encoded files for validation
  test_process.py          # Test detection pipeline
  test_ui_review.py        # Test Review tab: keep/discard/trim/custom_name
  test_ui_export.py        # Test Export tab: encoding, custom_name handling
  test_metadata.py         # Test metadata persistence (custom_name roundtrip)
```

## Fixtures (Pytest)

```python
@pytest.fixture
def app():
    """Create FastAPI test client."""
    output_dir = Path(tempfile.mkdtemp())
    app = create_app(output_dir)
    yield TestClient(app)
    shutil.rmtree(output_dir)

@pytest.fixture
def browser():
    """Selenium/Playwright browser instance."""
    driver = webdriver.Chrome()
    yield driver
    driver.quit()

@pytest.fixture
def test_video(tmp_path):
    """Generate or copy test video to temp location."""
    src = Path("tests/fixtures/videos/mixed_10s.mp4")
    if not src.exists():
        # Generate via FFmpeg: silence + noise + silence
        ...
    dst = tmp_path / "test.mp4"
    shutil.copy2(src, dst)
    return dst
```

## What NOT to Test

- **YouTube auth/upload**: Skip entirely (external dependency, requires real credentials)
- **Unit tests on individual detectors**: Trust existing code
- **FFmpeg encoding parameters**: Trust FFmpeg works
- **Performance benchmarks**: Not a priority for local tool
- **Error recovery**: Only test happy paths initially

## Running Tests

```bash
pytest tests/ -v --tb=short
pytest tests/test_ui_review.py -v  # Single file
```

## Success Criteria

- All 5 scenarios pass consistently
- No flaky tests (repeatable results)
- Execution time < 2 min total
- Clear failure messages (what went wrong, not just "assert failed")

## Future Enhancements (Not MVP)

- Headless browser mode (faster CI)
- Test video generation from scratch (avoid committed binaries)
- Load testing (can UI handle 100 clips?)
- Mock YouTube endpoint for upload tests

---

**Status**: Implemented. 21 tests across 4 files, all passing. Uses FastAPI TestClient (no browser automation needed). Run with `pytest tests/ -v`.
