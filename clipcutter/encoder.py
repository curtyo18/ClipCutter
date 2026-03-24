"""FFmpeg-based encoding for kept clips."""

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from clipcutter import config


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


def encode_clip(input_path: Path, output_path: Path,
                preset: EncodingPreset,
                target_fps: Optional[int] = None) -> Path:
    """Encode a clip using the given preset.

    Args:
        input_path: Path to the source clip.
        output_path: Path for the encoded output.
        preset: EncodingPreset with FFmpeg arguments.
        target_fps: Optional framerate override.

    Returns:
        Path to the encoded file.

    Raises:
        RuntimeError: If FFmpeg fails.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Copy preset with no FPS change: just copy the file
    if is_copy_preset(preset) and target_fps is None:
        shutil.copy2(str(input_path), str(output_path))
        return output_path

    cmd = ["ffmpeg", "-y", "-i", str(input_path)]

    if is_copy_preset(preset):
        # Copy preset but with FPS change requires re-encoding
        cmd.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "18",
                     "-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.extend(preset.ffmpeg_args)

    if target_fps is not None:
        cmd.extend(["-r", str(target_fps)])

    cmd.append(str(output_path))

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
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
