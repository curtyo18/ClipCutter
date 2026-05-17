"""Domain exceptions shared across modules.

Kept in its own module so audio/encoder/compiler can raise the same
timeout exception without forming an import cycle through any single
"primary" module.
"""


class FFmpegTimeoutError(RuntimeError):
    """ffmpeg/ffprobe exceeded the wall-clock timeout.

    Raised by audio/encoder/compiler wrappers when a subprocess.run or
    Popen.communicate call hits its timeout= ceiling. Callers (routes,
    workers) can catch this distinctly from a CalledProcessError to
    surface a "timed out" message rather than a generic encode failure.
    """
