"""Shared fixtures for ClipCutter integration tests."""

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipcutter.models import ClipMetadata
from clipcutter.web import create_app


# ---------------------------------------------------------------------------
# Test video generation via FFmpeg
# ---------------------------------------------------------------------------

_session_video_dir = None


@pytest.fixture(scope="session")
def video_dir():
    """Session-scoped directory with synthetic test videos.
    Cleaned up when the session ends."""
    global _session_video_dir
    _session_video_dir = Path(tempfile.mkdtemp(prefix="clipcutter_test_videos_"))
    yield _session_video_dir
    shutil.rmtree(_session_video_dir, ignore_errors=True)
    _session_video_dir = None


@pytest.fixture(scope="session")
def silence_video(video_dir):
    """5-second silent video — should produce no highlights."""
    path = video_dir / "silence_5s.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=5:r=10",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "64k",
        "-t", "5",
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return path


@pytest.fixture(scope="session")
def noise_video(video_dir):
    """10-second loud white noise video — should trigger sudden_noise detector."""
    path = video_dir / "noise_10s.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anoisesrc=d=10:c=white:a=0.8",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=10:r=10",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "64k",
        "-shortest",
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return path


@pytest.fixture(scope="session")
def mixed_video(video_dir):
    """10-second video: 3s silence + 4s loud noise + 3s silence."""
    path = video_dir / "mixed_10s.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        "anoisesrc=d=10:c=white:a=0.0001",
        "-f", "lavfi", "-i",
        "anoisesrc=d=4:c=white:a=0.9",
        "-filter_complex",
        "[0:a]volume=0.0001[quiet];"
        "[1:a]adelay=3000|3000[loud];"
        "[quiet][loud]amix=inputs=2:duration=first:normalize=0",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=10:r=10",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "64k",
        "-shortest",
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return path


# ---------------------------------------------------------------------------
# Output directory & app fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def output_dir():
    """Per-test temporary output directory. Cleaned up after each test."""
    d = Path(tempfile.mkdtemp(prefix="clipcutter_test_output_"))
    output = d / "output"
    yield output
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def app_client(output_dir):
    """FastAPI TestClient backed by a temp output directory."""
    app = create_app(output_dir)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers for setting up pre-populated state
# ---------------------------------------------------------------------------

def create_pending_clip(output_dir: Path, video_stem: str, filename: str,
                        source_video: str, start: float = 0.0,
                        end: float = 10.0, confidence: float = 0.8,
                        reasons: list = None) -> ClipMetadata:
    """Create a fake pending clip file and its metadata entry.

    Returns the ClipMetadata. Caller must call save_test_metadata() after
    adding all clips to write the JSON file.
    """
    reasons = reasons or ["volume_spike"]
    clip_dir = output_dir / "clips" / "pending" / video_stem
    clip_dir.mkdir(parents=True, exist_ok=True)

    # Create a tiny valid mp4 as the clip file
    clip_path = clip_dir / filename
    _make_tiny_mp4(clip_path)

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
    meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return meta_path


def keep_and_wait(client, video_stem: str, filename: str,
                  json_body: dict = None, timeout: float = 30.0) -> dict:
    """POST /api/clips/{stem}/{filename}/keep and block until the background
    worker finishes. Returns the final keep-task entry from the status endpoint.

    Phase 4 made the keep endpoint async — it returns {task_id, status="started"}
    immediately and the ffmpeg work happens in a daemon thread. Tests that need
    to check the resulting file/metadata state must wait for the task to land.

    ``client`` may be either a FastAPI/Starlette TestClient (in-process,
    relative paths) or a string base URL like ``"http://127.0.0.1:8123"``
    pointing at a live uvicorn server (browser tests). The latter goes
    through the same HTTP path the real frontend uses, so the AppState
    that's exercised is the one the browser is also hitting.
    """
    if isinstance(client, str):
        import requests
        base = client.rstrip("/")
        post = lambda path, **kw: requests.post(f"{base}{path}", **kw)
        get = lambda path, **kw: requests.get(f"{base}{path}", **kw)
    else:
        post = client.post
        get = client.get

    body = json_body if json_body is not None else {}
    resp = post(f"/api/clips/{video_stem}/{filename}/keep", json=body)
    if resp.status_code != 200:
        return {
            "status": "http_error",
            "_status_code": resp.status_code,
            "_text": resp.text,
        }
    task_id = resp.json().get("task_id")
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = get("/api/clips/keep/status").json()
        my = next((t for t in snap.get("tasks", []) if t["task_id"] == task_id), None)
        if my is None:
            # Task GC'd — backend treats it as finished.
            return {"status": "done", "task_id": task_id, "trimmed": False}
        if my["status"] in ("done", "error"):
            return my
        time.sleep(0.05)
    raise TimeoutError(f"keep task {task_id} did not finish within {timeout}s")


def _make_tiny_mp4(path: Path):
    """Create a tiny valid mp4 file (~1 second, black + silent)."""
    if path.exists():
        return
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
            "-f", "lavfi", "-i", "color=c=black:s=160x120:d=1:r=10",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "32k",
            "-t", "1",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
