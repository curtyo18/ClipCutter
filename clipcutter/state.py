"""Shared application state for ClipCutter web server."""
import threading
from pathlib import Path
from typing import Optional


class ProcessingState:
    """Thread-safe processing state."""

    def __init__(self):
        self.running = False
        self.log_lines: list[str] = []
        self.error: Optional[str] = None
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self.running = True
            self.log_lines = []
            self.error = None

    def add_line(self, line: str):
        with self._lock:
            self.log_lines.append(line)

    def finish(self, error: Optional[str] = None):
        with self._lock:
            self.running = False
            self.error = error

    def snapshot(self) -> dict:
        with self._lock:
            return {"running": self.running, "log": list(self.log_lines), "error": self.error}


class LogWriter:
    """Captures stdout writes into ProcessingState."""

    def __init__(self, state: ProcessingState, original):
        self.state = state
        self.original = original
        self._buf = ""

    def write(self, text: str):
        self.original.write(text)
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            stripped = line.strip()
            if stripped:
                self.state.add_line(stripped)

    def flush(self):
        self.original.flush()
        if self._buf.strip():
            self.state.add_line(self._buf.strip())
            self._buf = ""


class EncodingState:
    """Thread-safe encoding state."""

    def __init__(self):
        self.running = False
        self.current_file: Optional[str] = None
        self.current_index: int = 0
        self.total: int = 0
        self.completed: list[str] = []
        self.errors: list[dict] = []
        self.cancelled = False
        self._lock = threading.Lock()

    def reset(self, total: int):
        with self._lock:
            self.running = True
            self.current_file = None
            self.current_index = 0
            self.total = total
            self.completed = []
            self.errors = []
            self.cancelled = False

    def set_current(self, filename: str, index: int):
        with self._lock:
            self.current_file = filename
            self.current_index = index

    def add_completed(self, filename: str):
        with self._lock:
            self.completed.append(filename)

    def add_error(self, filename: str, error: str):
        with self._lock:
            self.errors.append({"filename": filename, "error": error})

    def finish(self):
        with self._lock:
            self.running = False
            self.current_file = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "current_file": self.current_file,
                "current_index": self.current_index,
                "total": self.total,
                "completed": list(self.completed),
                "errors": list(self.errors),
                "cancelled": self.cancelled,
            }


class UploadState:
    """Thread-safe upload state."""

    def __init__(self):
        self.running = False
        self.current_file: Optional[str] = None
        self.current_index: int = 0
        self.total: int = 0
        self.bytes_sent: int = 0
        self.bytes_total: int = 0
        self.completed: list[dict] = []
        self.errors: list[dict] = []
        self.cancelled = False
        self._lock = threading.Lock()

    def reset(self, total: int):
        with self._lock:
            self.running = True
            self.current_file = None
            self.current_index = 0
            self.total = total
            self.bytes_sent = 0
            self.bytes_total = 0
            self.completed = []
            self.errors = []
            self.cancelled = False

    def set_current(self, filename: str, index: int):
        with self._lock:
            self.current_file = filename
            self.current_index = index
            self.bytes_sent = 0
            self.bytes_total = 0

    def update_progress(self, bytes_sent: int, bytes_total: int):
        with self._lock:
            self.bytes_sent = bytes_sent
            self.bytes_total = bytes_total

    def add_completed(self, filename: str, video_id: str, url: str):
        with self._lock:
            self.completed.append({"filename": filename, "video_id": video_id, "url": url})

    def add_error(self, filename: str, error: str):
        with self._lock:
            self.errors.append({"filename": filename, "error": error})

    def finish(self):
        with self._lock:
            self.running = False
            self.current_file = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "current_file": self.current_file,
                "current_index": self.current_index,
                "total": self.total,
                "bytes_sent": self.bytes_sent,
                "bytes_total": self.bytes_total,
                "completed": list(self.completed),
                "errors": list(self.errors),
                "cancelled": self.cancelled,
            }


class CompilationState:
    """Thread-safe compilation build state."""

    def __init__(self):
        self.running = False
        self.current_step: str = ""
        self.progress_pct: float = 0
        self.completed = False
        self.error: Optional[str] = None
        self.output_filename: Optional[str] = None
        self.cancelled = False
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self.running = True
            self.current_step = "Starting..."
            self.progress_pct = 0
            self.completed = False
            self.error = None
            self.output_filename = None
            self.cancelled = False

    def update(self, step: str, pct: float):
        with self._lock:
            self.current_step = step
            self.progress_pct = pct

    def finish(self, filename: Optional[str] = None, error: Optional[str] = None):
        with self._lock:
            self.running = False
            self.completed = error is None
            self.error = error
            self.output_filename = filename

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "current_step": self.current_step,
                "progress_pct": self.progress_pct,
                "completed": self.completed,
                "error": self.error,
                "output_filename": self.output_filename,
                "cancelled": self.cancelled,
            }


class AppState:
    """Container for all shared state, instantiated once per app."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.proc = ProcessingState()
        self.enc = EncodingState()
        self.upl = UploadState()
        self.comp = CompilationState()
