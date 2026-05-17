"""FFmpeg wrappers for audio extraction and clip cutting."""

import shutil
import subprocess
from pathlib import Path

from clipcutter.config import AUDIO_SAMPLE_RATE
from clipcutter.errors import FFmpegTimeoutError

# Wall-clock ceilings for ffmpeg/ffprobe invocations. A corrupt MP4 can
# stall the underlying process forever — without a timeout the worker
# thread hangs and the UI sits at the current step indefinitely. Values
# are generous enough for healthy inputs but small enough that a stuck
# call surfaces quickly.
FFPROBE_TIMEOUT = 30                # short metadata queries
FFMPEG_EXTRACT_AUDIO_TIMEOUT = 600  # 10 min ceiling for the full-track WAV
FFMPEG_EXTRACT_CLIP_TIMEOUT = 60    # `-c copy` is essentially I/O bound


class NoAudioStreamError(Exception):
    pass


def check_ffmpeg():
    """Verify ffmpeg and ffprobe are available."""
    for cmd in ("ffmpeg", "ffprobe"):
        if not shutil.which(cmd):
            raise RuntimeError(
                f"{cmd} not found on PATH. Install FFmpeg: https://ffmpeg.org/download.html"
            )


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, check=True,
            timeout=FFPROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegTimeoutError(
            f"ffprobe timed out after {FFPROBE_TIMEOUT}s reading duration of {video_path}"
        ) from exc
    return float(result.stdout.strip())


def extract_audio(video_path: Path, output_dir: Path,
                  sample_rate: int = AUDIO_SAMPLE_RATE) -> Path:
    """Extract mono WAV audio from a video file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / f"{video_path.stem}.wav"

    # Skip if already extracted and video hasn't changed
    if wav_path.exists():
        if wav_path.stat().st_mtime >= video_path.stat().st_mtime:
            return wav_path

    # Check for audio stream. check=True so a probe failure (corrupt file,
    # bad path, unsupported container) raises CalledProcessError instead of
    # being misreported downstream as "No audio stream found" — that
    # message is reserved for the case where ffprobe runs successfully
    # but reports no audio streams in the file.
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        str(video_path),
    ]
    try:
        probe = subprocess.run(
            probe_cmd,
            capture_output=True, text=True, check=True,
            timeout=FFPROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegTimeoutError(
            f"ffprobe timed out after {FFPROBE_TIMEOUT}s probing audio stream of {video_path}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr_tail = (exc.stderr or "")[-500:]
        raise RuntimeError(
            f"ffprobe failed to probe {video_path}: {stderr_tail}"
        ) from exc
    if not probe.stdout.strip():
        raise NoAudioStreamError(f"No audio stream in {video_path}")

    extract_cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        str(wav_path),
    ]
    try:
        subprocess.run(
            extract_cmd,
            capture_output=True, check=True,
            timeout=FFMPEG_EXTRACT_AUDIO_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegTimeoutError(
            f"ffmpeg timed out after {FFMPEG_EXTRACT_AUDIO_TIMEOUT}s extracting audio from {video_path}"
        ) from exc
    return wav_path


# Substrings (case-insensitive) in ffmpeg stderr that justify falling back
# to the re-encode path. Anything else (typo in path, missing source,
# permission denied, etc.) should surface as a real error instead of
# silently triggering a re-encode that will fail with the same message.
_STREAM_COPY_FALLBACK_PATTERNS = (
    "stream specifier",
    "could not find tag for codec",
    "container does not support",
    "not supported",
)


def _is_recoverable_stream_copy_error(stderr: str) -> bool:
    """Return True if stderr matches a known codec/container incompatibility
    that the re-encode fallback can fix."""
    if not stderr:
        return False
    lowered = stderr.lower()
    return any(p in lowered for p in _STREAM_COPY_FALLBACK_PATTERNS)


def extract_clip(video_path: Path, start: float, end: float,
                 output_path: Path) -> Path:
    """Extract a clip from a video using stream copy (fast, keyframe-aligned)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = end - start

    copy_cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(video_path),
        "-t", f"{duration:.3f}",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        str(output_path),
    ]
    try:
        result = subprocess.run(
            copy_cmd,
            capture_output=True, text=True,
            timeout=FFMPEG_EXTRACT_CLIP_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegTimeoutError(
            f"ffmpeg timed out after {FFMPEG_EXTRACT_CLIP_TIMEOUT}s extracting clip from {video_path}"
        ) from exc

    # If stream copy fails, only fall back to re-encoding when stderr matches
    # a known codec/container-incompatibility pattern. Other failures (bad
    # path, missing source, permission denied) used to cascade silently into
    # the re-encode branch and surface as the same generic error twice — now
    # they raise with the real stderr so the root cause is visible.
    if result.returncode != 0:
        stderr = result.stderr or ""
        if not _is_recoverable_stream_copy_error(stderr):
            stderr_tail = stderr[-500:]
            raise RuntimeError(
                f"ffmpeg stream-copy failed: {stderr_tail}"
            )

        reencode_cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-i", str(video_path),
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-c:a", "aac",
            "-avoid_negative_ts", "make_zero",
            str(output_path),
        ]
        try:
            subprocess.run(
                reencode_cmd,
                capture_output=True, check=True,
                timeout=FFMPEG_EXTRACT_CLIP_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise FFmpegTimeoutError(
                f"ffmpeg timed out after {FFMPEG_EXTRACT_CLIP_TIMEOUT}s re-encoding clip from {video_path}"
            ) from exc

    return output_path
