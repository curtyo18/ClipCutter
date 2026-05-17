"""FFmpeg-based encoding for kept clips."""

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from clipcutter import config
from clipcutter.errors import FFmpegTimeoutError

# Flat 10-minute ceiling on a single encode_clip() call. The encode
# worker in routes/encode.py has its own (matching) Popen-based ceiling
# so its cancel logic stays in charge; this constant just protects
# ad-hoc callers (tests, CLI experiments) from hangs on corrupt input.
FFMPEG_ENCODE_TIMEOUT = 600


@dataclass
class EncodingPreset:
    """A named FFmpeg encoding preset."""
    name: str
    display_name: str
    extension: str
    ffmpeg_args: List[str] = field(default_factory=list)


def get_presets() -> dict:
    """Build EncodingPreset instances from config.ENCODING_PRESETS."""
    presets = {}
    for name, spec in config.ENCODING_PRESETS.items():
        presets[name] = EncodingPreset(
            name=name,
            display_name=spec["display_name"],
            extension=spec["extension"],
            ffmpeg_args=list(spec["ffmpeg_args"]),
        )
    return presets


def is_copy_preset(preset: EncodingPreset) -> bool:
    """Check if this preset is a straight copy (no re-encoding)."""
    return not preset.ffmpeg_args


def build_encode_command(input_path: Path, output_path: Path,
                         preset: EncodingPreset,
                         target_fps: Optional[int] = None,
                         slowdown_factor: Optional[float] = None
                         ) -> Optional[List[str]]:
    """Build the ffmpeg argv for encoding a clip with the given preset.

    Returns None for the no-op "copy preset, no fps/slowdown change" case —
    callers should shutil.copy2 the file directly in that case (the encode
    worker handles both branches so it can stay in control of cancellation
    and Popen tracking).
    """
    if is_copy_preset(preset) and target_fps is None and slowdown_factor is None:
        return None

    cmd = ["ffmpeg", "-y", "-i", str(input_path)]

    if preset.name == "gif":
        vf = list(preset.ffmpeg_args)  # Starts with palette generation
        if slowdown_factor and slowdown_factor != 1.0:
            # Insert slowdown into filter chain: setpts=PTS/factor
            # Replace the palette filter to include slowdown. The non-slowdown
            # preset adds `-an` (GIF has no audio); add it here too so the
            # slowdown branch doesn't emit a stream-not-mappable warning or
            # silently keep an audio track on a GIF container.
            base_filter = (
                f"split=2[m0][m1];[m0]palettegen[p];"
                f"[m1]setpts=PTS/{slowdown_factor}[s];[s][p]paletteuse"
            )
            cmd.extend(["-vf", base_filter, "-an"])
        else:
            cmd.extend(vf)
        cmd.append(str(output_path))
    else:
        if is_copy_preset(preset):
            # Copy preset but with FPS change requires re-encoding
            cmd.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.extend(preset.ffmpeg_args)

        if target_fps is not None:
            cmd.extend(["-r", str(target_fps)])

        cmd.append(str(output_path))

    return cmd


def encode_clip(input_path: Path, output_path: Path,
                preset: EncodingPreset,
                target_fps: Optional[int] = None,
                slowdown_factor: Optional[float] = None) -> Path:
    """Encode a clip synchronously using the given preset.

    Kept as a convenience for non-worker callers (e.g. ad-hoc CLI use,
    tests). The encode worker in routes/encode.py uses build_encode_command
    + its own Popen so it can manage cancellation.

    Args:
        input_path: Path to the source clip.
        output_path: Path for the encoded output.
        preset: EncodingPreset with FFmpeg arguments.
        target_fps: Optional framerate override (video only).
        slowdown_factor: Optional playback speed factor (0.5 = half speed, only for GIF).

    Returns:
        Path to the encoded file.

    Raises:
        RuntimeError: If FFmpeg fails.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = build_encode_command(input_path, output_path, preset, target_fps, slowdown_factor)
    if cmd is None:
        shutil.copy2(str(input_path), str(output_path))
        return output_path

    try:
        subprocess.run(
            cmd, capture_output=True, text=True, check=True,
            timeout=FFMPEG_ENCODE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass
        raise FFmpegTimeoutError(
            f"FFmpeg encoding timed out after {FFMPEG_ENCODE_TIMEOUT}s for {input_path.name}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        # Clean up partial output file
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass
        stderr_tail = (exc.stderr or "")[-500:]
        raise RuntimeError(
            f"FFmpeg encoding failed for {input_path.name}: {stderr_tail}"
        ) from exc

    return output_path
