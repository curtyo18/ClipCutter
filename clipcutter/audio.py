"""FFmpeg wrappers for audio extraction and clip cutting."""

import shutil
import subprocess
from pathlib import Path

from clipcutter.config import AUDIO_SAMPLE_RATE


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
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(video_path),
        ],
        capture_output=True, text=True, check=True,
    )
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

    # Check for audio stream
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(video_path),
        ],
        capture_output=True, text=True,
    )
    if not probe.stdout.strip():
        raise NoAudioStreamError(f"No audio stream in {video_path}")

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", "1",
            str(wav_path),
        ],
        capture_output=True, check=True,
    )
    return wav_path


def extract_clip(video_path: Path, start: float, end: float,
                 output_path: Path) -> Path:
    """Extract a clip from a video using stream copy (fast, keyframe-aligned)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = end - start

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-i", str(video_path),
            "-t", f"{duration:.3f}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            str(output_path),
        ],
        capture_output=True, text=True,
    )

    # If stream copy fails, fall back to re-encoding
    if result.returncode != 0:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", f"{start:.3f}",
                "-i", str(video_path),
                "-t", f"{duration:.3f}",
                "-c:v", "libx264", "-c:a", "aac",
                "-avoid_negative_ts", "make_zero",
                str(output_path),
            ],
            capture_output=True, check=True,
        )

    return output_path
