"""Shared application state for ClipCutter web server."""
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class ProcessingState:
    """Thread-safe processing state."""

    def __init__(self):
        self.running = False
        self.log_lines: list[str] = []
        self.error: Optional[str] = None
        self.videos_total: int = 0
        self.videos_done: int = 0
        self.current_video: Optional[str] = None
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self.running = True
            self.log_lines = []
            self.error = None
            self.videos_total = 0
            self.videos_done = 0
            self.current_video = None

    def add_line(self, line: str):
        with self._lock:
            self.log_lines.append(line)

    def set_total(self, n: int):
        with self._lock:
            self.videos_total = n

    def start_video(self, name: str):
        with self._lock:
            self.current_video = name

    def finish_video(self):
        with self._lock:
            self.videos_done += 1
            self.current_video = None

    def finish(self, error: Optional[str] = None):
        with self._lock:
            self.running = False
            self.error = error

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "log": list(self.log_lines),
                "error": self.error,
                "videos_total": self.videos_total,
                "videos_done": self.videos_done,
                "current_video": self.current_video,
            }


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
    """Thread-safe encoding state.

    cancel_event drives between-iteration cancellation; popen lets the
    cancel route .terminate() the in-flight ffmpeg subprocess instead of
    waiting for it to finish. The .cancelled property keeps the legacy
    bool-ish API working for routes/snapshot consumers (notably the FE,
    which expects {"cancelled": bool} in the status payload).
    """

    def __init__(self):
        self.running = False
        self.current_file: Optional[str] = None
        self.current_index: int = 0
        self.total: int = 0
        self.completed: list[str] = []
        self.errors: list[dict] = []
        self.cancel_event = threading.Event()
        self.popen: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    @cancelled.setter
    def cancelled(self, value: bool) -> None:
        if value:
            self.cancel_event.set()
        else:
            self.cancel_event.clear()

    def reset(self, total: int):
        with self._lock:
            self.running = True
            self.current_file = None
            self.current_index = 0
            self.total = total
            self.completed = []
            self.errors = []
            self.popen = None
            # cancel_event manipulation is atomic — safe to clear outside
            # the lock, but keep it here to preserve the "reset is one
            # consistent snapshot" invariant.
            self.cancel_event.clear()

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

    def set_popen(self, popen: Optional[subprocess.Popen]) -> None:
        """Register/clear the currently-running ffmpeg subprocess."""
        with self._lock:
            self.popen = popen

    def get_popen(self) -> Optional[subprocess.Popen]:
        with self._lock:
            return self.popen

    def finish(self):
        with self._lock:
            self.running = False
            self.current_file = None
            self.popen = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "current_file": self.current_file,
                "current_index": self.current_index,
                "total": self.total,
                "completed": list(self.completed),
                "errors": list(self.errors),
                "cancelled": self.cancel_event.is_set(),
            }


class UploadState:
    """Thread-safe upload state.

    cancel_event drives between-chunk cancellation inside upload_video,
    so a stalled-but-eventually-progressing TCP transfer can be aborted
    without waiting for the next clip. The .cancelled property keeps
    the legacy bool-ish API working for routes/snapshot consumers (the
    FE expects {"cancelled": bool} in the status payload), same pattern
    as EncodingState.
    """

    def __init__(self):
        self.running = False
        self.current_file: Optional[str] = None
        self.current_index: int = 0
        self.total: int = 0
        self.bytes_sent: int = 0
        self.bytes_total: int = 0
        self.completed: list[dict] = []
        self.errors: list[dict] = []
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    @cancelled.setter
    def cancelled(self, value: bool) -> None:
        if value:
            self.cancel_event.set()
        else:
            self.cancel_event.clear()

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
            # cancel_event manipulation is atomic — clearing under the
            # lock keeps "reset is one consistent snapshot" intact.
            self.cancel_event.clear()

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
                "cancelled": self.cancel_event.is_set(),
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


@dataclass
class KeepTask:
    """One in-flight keep (trim) operation.

    cancel_event + popen mirror EncodingState's pattern but per-task,
    since keep workers can run in parallel (different clips, different
    files). The worker checks cancel_event between subprocess spawns
    and stores its current Popen here so the cancel route can terminate
    the in-flight ffmpeg.
    """
    task_id: str
    video_stem: str
    filename: str
    status: str = "running"  # running | done | error | cancelled
    progress_step: str = "Starting…"
    error: Optional[str] = None
    trimmed: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    popen: Optional[subprocess.Popen] = None


class KeepState:
    """Per-task state for trim/keep operations.

    Unlike the singleton kinds (process / encode / compile / upload), keep
    can run multiple in parallel — different clips, different files. Each
    task is keyed by a UUID; finished tasks are kept around briefly so
    the FE can fetch the final state, then garbage-collected.
    """

    GC_AFTER_SECONDS = 60.0

    def __init__(self):
        self.tasks: dict[str, KeepTask] = {}
        self._lock = threading.Lock()

    def start(self, video_stem: str, filename: str) -> str:
        tid = uuid.uuid4().hex
        with self._lock:
            self.tasks[tid] = KeepTask(task_id=tid, video_stem=video_stem, filename=filename)
        return tid

    def get(self, tid: str) -> Optional[KeepTask]:
        with self._lock:
            return self.tasks.get(tid)

    def update_step(self, tid: str, step: str) -> None:
        with self._lock:
            t = self.tasks.get(tid)
            if t and t.status == "running":
                t.progress_step = step

    def set_popen(self, tid: str, popen: Optional[subprocess.Popen]) -> None:
        """Register/clear the running ffmpeg subprocess for this task."""
        with self._lock:
            t = self.tasks.get(tid)
            if t:
                t.popen = popen

    def get_popen(self, tid: str) -> Optional[subprocess.Popen]:
        with self._lock:
            t = self.tasks.get(tid)
            return t.popen if t else None

    def get_cancel_event(self, tid: str) -> Optional[threading.Event]:
        with self._lock:
            t = self.tasks.get(tid)
            return t.cancel_event if t else None

    def cancel(self, tid: str) -> bool:
        """Signal cancellation for a task. Returns True if the task exists."""
        with self._lock:
            t = self.tasks.get(tid)
            if not t:
                return False
            t.cancel_event.set()
            return True

    def finish(self, tid: str, error: Optional[str] = None, trimmed: bool = False,
               cancelled: bool = False) -> None:
        with self._lock:
            t = self.tasks.get(tid)
            if not t:
                return
            if cancelled:
                t.status = "cancelled"
            else:
                t.status = "error" if error else "done"
            t.error = error
            t.trimmed = trimmed
            t.finished_at = time.time()
            t.popen = None

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            # Drop tasks that finished a while ago to keep state bounded.
            self.tasks = {
                tid: t for tid, t in self.tasks.items()
                if t.finished_at is None or (now - t.finished_at) < self.GC_AFTER_SECONDS
            }
            return {
                "tasks": [
                    {
                        "task_id": t.task_id,
                        "video_stem": t.video_stem,
                        "filename": t.filename,
                        "status": t.status,
                        "progress_step": t.progress_step,
                        "error": t.error,
                        "trimmed": t.trimmed,
                    }
                    for t in self.tasks.values()
                ]
            }


class AppState:
    """Container for all shared state, instantiated once per app."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.proc = ProcessingState()
        self.enc = EncodingState()
        self.upl = UploadState()
        self.comp = CompilationState()
        self.keep = KeepState()
