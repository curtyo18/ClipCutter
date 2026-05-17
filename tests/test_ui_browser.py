"""Browser-based UI tests using Playwright (Scenarios 1, 3, 4, 5).

These tests launch a real server and drive the UI with a headless Chromium
browser, validating that the SPA tabs, buttons, and workflows function
correctly end-to-end.
"""

import json
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect

from clipcutter.web import create_app
from tests.conftest import create_pending_clip, save_test_metadata, keep_and_wait

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _free_port():
    """Find a free TCP port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_app():
    """Start a real uvicorn server on a random port; yield (url, output_dir).
    Temp directory is cleaned up after the test."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="clipcutter_browser_test_"))
    output_dir = tmp_dir / "output"
    app = create_app(output_dir)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait until the server is accepting connections
    deadline = time.time() + 10
    import socket
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        pytest.fail("Server did not start in time")

    yield f"http://127.0.0.1:{port}", output_dir

    server.should_exit = True
    thread.join(timeout=5)
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def browser_page(live_app):
    """Playwright page connected to the live app. Returns (page, url, output_dir)."""
    from playwright.sync_api import sync_playwright
    url, output_dir = live_app
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    yield page, url, output_dir
    browser.close()
    pw.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTabNavigation:
    """Verify the three tabs render and switch correctly."""

    def test_tabs_switch(self, browser_page):
        page, url, _ = browser_page
        page.goto(url)

        # Process tab is active by default
        assert page.locator("#view-process").is_visible()
        assert not page.locator("#view-review").is_visible()

        # Switch to Review tab
        page.click('[data-tab="review"]')
        expect(page.locator("#view-review")).to_be_visible()
        expect(page.locator("#view-process")).not_to_be_visible()

        # Switch to Export tab
        page.click('[data-tab="export"]')
        expect(page.locator("#view-export")).to_be_visible()
        expect(page.locator("#view-review")).not_to_be_visible()


class TestProcessTab:
    """Verify the Process tab form elements exist and process button works."""

    def test_process_form_elements(self, browser_page):
        page, url, _ = browser_page
        page.goto(url)

        assert page.locator("#folderPath").is_visible()
        assert page.locator("#sensitivity").is_visible()
        assert page.locator("#context").is_visible()
        assert page.locator("#btnProcess").is_visible()
        assert page.locator("#logBox").is_visible()

    def test_process_empty_folder_shows_alert(self, browser_page):
        page, url, _ = browser_page
        page.goto(url)

        # Clear the folder field (it may have a default from /api/defaults)
        page.fill("#folderPath", "")

        # Handle the alert dialog
        alert_message = []
        page.on("dialog", lambda d: (alert_message.append(d.message), d.accept()))

        with page.expect_event("dialog") as dialog_info:
            page.click("#btnProcess")
        dialog_info.value  # ensure the dialog event resolved

        assert len(alert_message) == 1
        assert "folder" in alert_message[0].lower() or "Enter" in alert_message[0]


class TestReviewTabBrowser:
    """Test the Review tab workflow via browser: keep, discard, custom name."""

    def test_empty_review_shows_message(self, browser_page):
        page, url, _ = browser_page
        page.goto(url)
        page.click('[data-tab="review"]')

        expect(page.locator("#reviewContent")).to_contain_text("No pending clips")

    def test_review_shows_clip(self, browser_page):
        page, url, output_dir = browser_page

        # Set up a pending clip
        stem = "browsevid"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/browsevid.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/browsevid.mp4")

        page.goto(url)
        page.click('[data-tab="review"]')

        # Should show the clip with Keep/Skip/Discard buttons
        expect(page.locator(".cc-action-keep")).to_be_visible()
        expect(page.locator(".cc-action-skip")).to_be_visible()
        expect(page.locator(".cc-action-discard")).to_be_visible()

        # Should show detection info (region label is "volume" + "% confidence")
        text = page.locator("#reviewContent").inner_text().lower()
        assert "volume" in text
        assert "confidence" in text

    def test_keep_clip_via_button(self, browser_page):
        page, url, output_dir = browser_page

        stem = "keepbtn"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/keepbtn.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/keepbtn.mp4")

        page.goto(url)
        page.click('[data-tab="review"]')

        # Wait for the clip UI to render
        expect(page.locator("#clipCustomName")).to_be_visible()

        # Fill custom name
        page.fill("#clipCustomName", "My Browser Clip")

        # Click Keep
        page.click(".cc-action-keep")

        # Should show "Review Complete" since there was only 1 clip
        expect(page.locator("#reviewContent")).to_contain_text(
            re.compile(r"review complete", re.IGNORECASE)
        )

        # Verify file was kept and custom name saved (Phase 4 made keep async,
        # so we may need to wait briefly for the daemon worker to land).
        # NOTE: we intentionally do NOT assert "1 kept" in the UI text — the
        # frontend updates that counter optimistically on click, so it always
        # passes regardless of backend success. _wait_for_path is the real
        # load-bearing check that the keep worker actually ran.
        kept_path = output_dir / "clips" / "kept" / stem / "clip_001.mp4"
        _wait_for_path(kept_path, timeout=5)

        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["status"] == "kept"
        assert meta["clips"][0]["custom_name"] == "My Browser Clip"

    def test_discard_clip_via_button(self, browser_page):
        page, url, output_dir = browser_page

        stem = "discardbtn"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/discardbtn.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/discardbtn.mp4")

        page.goto(url)
        page.click('[data-tab="review"]')
        expect(page.locator(".cc-action-discard")).to_be_visible()

        page.click(".cc-action-discard")

        expect(page.locator("#reviewContent")).to_contain_text(
            re.compile(r"review complete", re.IGNORECASE)
        )
        expect(page.locator("#reviewContent")).to_contain_text("1 discarded")

        meta = _load_meta(output_dir, stem)
        assert meta["clips"][0]["status"] == "discarded"

    def test_keyboard_shortcuts(self, browser_page):
        """Verify K/S/D keyboard shortcuts work for clip actions."""
        page, url, output_dir = browser_page

        stem = "kbdvid"
        clips = [
            create_pending_clip(output_dir, stem, f"clip_{i:03d}.mp4",
                                source_video="/fake/kbdvid.mp4",
                                confidence=0.9 - i * 0.1)
            for i in range(3)
        ]
        save_test_metadata(output_dir, stem, clips, "/fake/kbdvid.mp4")

        page.goto(url)
        page.click('[data-tab="review"]')
        # Wait for the first clip to render before driving keyboard shortcuts.
        expect(page.locator(".cc-action-keep")).to_be_visible()
        # Position indicator's first inner span is the current index (1-based).
        pos = page.locator(".cc-review-pos > span").first
        expect(pos).to_have_text("1")

        # K = keep first clip; UI advances to clip 2/3.
        page.keyboard.press("k")
        expect(pos).to_have_text("2")

        # S = skip second clip; UI advances to clip 3/3.
        page.keyboard.press("s")
        expect(pos).to_have_text("3")

        # D = discard third clip; should reach "review complete".
        page.keyboard.press("d")
        expect(page.locator("#reviewContent")).to_contain_text(
            re.compile(r"review complete", re.IGNORECASE)
        )
        expect(page.locator("#reviewContent")).to_contain_text("1 discarded")
        expect(page.locator("#reviewContent")).to_contain_text("1 skipped")

        # Wait for the keep worker (the K shortcut keeps clip 0)
        kept_path = output_dir / "clips" / "kept" / stem / "clip_000.mp4"
        _wait_for_path(kept_path, timeout=5)


class TestExportTabBrowser:
    """Test the Export tab displays kept clips and preset controls."""

    def test_export_shows_kept_clips(self, browser_page):
        page, url, output_dir = browser_page

        # Create a kept clip
        stem = "expvid"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/expvid.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/expvid.mp4")

        # Keep it via the live server's HTTP API (the same backend the browser
        # hits) and wait for the async worker to land before driving the UI.
        # Using a second TestClient(create_app(output_dir)) here would build a
        # parallel AppState whose state.keep is invisible to the live server.
        keep_and_wait(url, stem, "clip_001.mp4",
                      json_body={"trim_start": 0.0, "trim_end": 0.0,
                                 "custom_name": "Export Test"})

        page.goto(url)
        page.click('[data-tab="export"]')

        # Should show the clip in the export list
        expect(page.locator("#view-export")).to_contain_text("clip_001.mp4")

        # Should show preset selector
        expect(page.locator("#encodePreset")).to_be_visible()

    def test_export_preset_options(self, browser_page):
        page, url, _ = browser_page
        page.goto(url)
        page.click('[data-tab="export"]')
        expect(page.locator("#encodePreset")).to_be_visible()

        # Verify all presets appear in dropdown
        options = page.locator("#encodePreset option").all_text_contents()
        option_text = " ".join(options).lower()
        assert "original" in option_text
        assert "high" in option_text
        assert "low" in option_text
        assert "gif" in option_text


class TestPlayerVolumePersistence:
    """Volume slider should persist across reloads via localStorage and apply
    to both the Review tab player and the inline preview modal."""

    def test_review_player_reads_saved_volume(self, browser_page):
        page, url, output_dir = browser_page

        stem = "volclip"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/volclip.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/volclip.mp4")

        page.goto(url)
        page.evaluate("localStorage.setItem('cc.playerVolume', '0.2')")
        page.reload()
        page.click('[data-tab="review"]')

        # wait_for_function below auto-polls and handles the element appearing.
        page.wait_for_function(
            """() => {
                const v = document.getElementById('player');
                return v && Math.abs(v.volume - 0.2) < 1e-3;
            }""",
            timeout=5000,
        )

    def test_preview_modal_uses_saved_volume(self, browser_page):
        page, url, output_dir = browser_page

        stem = "volpreview"
        clip = create_pending_clip(
            output_dir, stem, "clip_001.mp4",
            source_video="/fake/volpreview.mp4",
        )
        save_test_metadata(output_dir, stem, [clip], "/fake/volpreview.mp4")

        # Mark the clip as kept (via the live server's HTTP API — same backend
        # the browser hits) so it shows up on the Export tab.
        keep_and_wait(url, stem, "clip_001.mp4",
                      json_body={"segments": [], "custom_name": None})

        page.goto(url)
        page.evaluate("localStorage.setItem('cc.playerVolume', '0.2')")
        page.reload()
        page.click('[data-tab="export"]')

        # Wait for the kept clip to appear in the export list — previewClip(0)
        # below depends on the keptClips array being populated.
        expect(page.locator("#view-export")).to_contain_text("clip_001.mp4")

        # Open the inline preview modal by invoking the handler directly. The
        # filename-click wiring is exercised elsewhere — here we're just
        # verifying that the modal's <video> picks up the stored volume.
        page.evaluate("window._cc.previewClip(0)")

        page.wait_for_function(
            """() => {
                const m = document.getElementById('clipPreviewModal');
                if (!m) return false;
                const v = m.querySelector('video');
                return v && Math.abs(v.volume - 0.2) < 1e-3;
            }""",
            timeout=5000,
        )


class TestProcessProgressMovement:
    """The chip should show non-trivial pct movement during a multi-video
    folder run — both from the new real per-video signal and from the
    asymptotic fallback that smooths the bar between data points."""

    def test_chip_pct_moves_during_run(self, browser_page, silence_video, mixed_video):
        page, url, output_dir = browser_page

        proc_dir = output_dir / "progress_src"
        proc_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(silence_video), str(proc_dir / "silence_5s.mp4"))
        shutil.copy2(str(mixed_video), str(proc_dir / "mixed_10s.mp4"))

        page.goto(url)
        page.fill("#folderPath", str(proc_dir))
        page.click("#btnProcess")

        # Assert real progress motion: the chip must move past 0% (proving the
        # progress signal is wired up) AND cross 50% (proving the bar advances
        # rather than just jumping 0 -> 100). We avoid asserting a specific
        # number of distinct values because fast hosts can finish the whole
        # pipeline in <1.6s and only ever publish 0% and 100%.
        page.wait_for_function(
            """() => {
                const el = document.querySelector('.cc-task-chip-pct');
                if (!el) return false;
                const m = /(\\d+)\\s*%/.exec(el.textContent || '');
                return !!m && parseInt(m[1], 10) > 0;
            }""",
            timeout=10000,
        )
        page.wait_for_function(
            """() => {
                const el = document.querySelector('.cc-task-chip-pct');
                if (!el) return false;
                const m = /(\\d+)\\s*%/.exec(el.textContent || '');
                return !!m && parseInt(m[1], 10) > 50;
            }""",
            timeout=30000,
        )


class TestProcessTabRefreshOnActivation:
    """When a source is deleted from another tab (or filesystem), switching
    back to the Process tab should re-scan the folder so stale entries vanish.
    """

    def test_process_rescans_when_tab_activated(self, browser_page, silence_video):
        page, url, output_dir = browser_page

        proc_dir = output_dir / "rescan_src"
        proc_dir.mkdir(parents=True, exist_ok=True)
        target = proc_dir / "to_be_deleted.mp4"
        shutil.copy2(str(silence_video), str(target))

        page.goto(url)
        page.fill("#folderPath", str(proc_dir))
        page.click("#btnScan")

        # Wait for the row to appear in the table after the initial scan.
        page.wait_for_function(
            """name => {
                const sec = document.getElementById('videosInFolderSection');
                return sec && sec.innerText.includes(name);
            }""",
            arg="to_be_deleted.mp4",
            timeout=5000,
        )

        # Simulate the cross-tab delete by removing the file from disk.
        # The Process tab must re-scan when re-activated and drop the stale row.
        target.unlink()

        page.click('[data-tab="review"]')
        expect(page.locator("#view-review")).to_be_visible()
        page.click('[data-tab="process"]')

        # The deleted filename should disappear from the videos-in-folder table.
        page.wait_for_function(
            """name => {
                const sec = document.getElementById('videosInFolderSection');
                return sec && !sec.innerText.includes(name);
            }""",
            arg="to_be_deleted.mp4",
            timeout=5000,
        )


class TestFullBrowserWorkflow:
    """Scenario 1 end-to-end via browser: process -> review -> keep -> export."""

    def test_process_review_export(self, browser_page, mixed_video):
        page, url, output_dir = browser_page

        # 1. Process tab: enter folder and process
        proc_dir = output_dir / "source"
        proc_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(mixed_video), str(proc_dir / "mixed_10s.mp4"))

        page.goto(url)
        page.fill("#folderPath", str(proc_dir))
        page.click("#btnProcess")

        # Wait for processing to complete (the task-complete handler appends
        # a "[done]" log tag in the new design)
        page.wait_for_function(
            """() => {
                const box = document.getElementById('logBox');
                return box && box.innerHTML.includes('[done]');
            }""",
            timeout=120000,
        )

        # 2. Review tab: verify clip appears
        page.click('[data-tab="review"]')

        expect(page.locator(".cc-action-keep")).to_be_visible()

        # Set custom name and keep
        page.fill("#clipCustomName", "Browser Full Test")
        page.click(".cc-action-keep")

        # Should reach "Review Complete"
        expect(page.locator("#reviewContent")).to_contain_text(
            re.compile(r"review complete", re.IGNORECASE)
        )

        # 3. Export tab: verify clip in export list
        page.click('[data-tab="export"]')

        expect(page.locator("#view-export")).to_contain_text(
            re.compile(r"mixed_10s|clip_", re.IGNORECASE)
        )

        # Verify metadata has custom name (poll until the async keep worker
        # finishes — Phase 4 made it asynchronous)
        meta_dir = output_dir / "metadata"
        meta = _wait_for_keep_with_custom_name(meta_dir, "Browser Full Test")
        kept_clips = [c for c in meta["clips"] if c["status"] == "kept"]
        assert len(kept_clips) >= 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_meta(output_dir: Path, video_stem: str) -> dict:
    meta_path = output_dir / "metadata" / f"{video_stem}_clips.json"
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _wait_for_path(path: Path, timeout: float = 5.0) -> None:
    """Poll until the given path exists or timeout. Used to wait for the
    async keep worker (Phase 4) to finish copying a clip into kept/."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"Path {path} did not appear within {timeout}s")


def _wait_for_keep_with_custom_name(meta_dir: Path, custom_name: str,
                                    timeout: float = 10.0) -> dict:
    """Poll any *_clips.json under meta_dir for a kept clip with the given
    custom_name. Returns the matching metadata dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for meta_file in meta_dir.glob("*_clips.json"):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                for c in meta.get("clips", []):
                    if c.get("status") == "kept" and c.get("custom_name") == custom_name:
                        return meta
            except (json.JSONDecodeError, OSError):
                continue
        time.sleep(0.1)
    raise AssertionError(
        f"No kept clip with custom_name='{custom_name}' in {meta_dir} after {timeout}s"
    )
