"""Background work for the GUI: the job queue thread and the live translator bridge (thread callbacks → Qt signals)."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QThread, Signal

from ..pipeline import Job, run_job
from ..realtime import Caption, LiveTranslator
from ..settings import Settings


class JobQueue(QThread):
    """Runs queued jobs one at a time, in the order added. Settings are read when each job starts."""
    job_changed = Signal(object, str)      # job, message
    log = Signal(str)
    idle = Signal()

    def __init__(self, settings_getter, parent=None):
        super().__init__(parent)
        self._settings_getter = settings_getter
        self._jobs: list[Job] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._cancel_current = threading.Event()
        self._current: Job | None = None
        self._quit = False

    # ---------------------------------------------------------------- called from the GUI thread
    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs.append(job)
        self._wake.set()

    def remove(self, job: Job) -> None:
        with self._lock:
            if job in self._jobs and job is not self._current:
                self._jobs.remove(job)

    def cancel(self, job: Job) -> None:
        if job is self._current:
            self._cancel_current.set()
        elif job.status == "queued":
            job.status = "cancelled"
            job.stage = "Cancelled"
            self.job_changed.emit(job, "")

    def retry(self, job: Job) -> None:
        job.status, job.progress, job.error, job.stage = "queued", 0.0, "", ""
        with self._lock:
            if job in self._jobs:
                self._jobs.remove(job)
            self._jobs.append(job)
        self.job_changed.emit(job, "")
        self._wake.set()

    @property
    def busy(self) -> bool:
        return self._current is not None

    def pending(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs if j.status == "queued")

    def stop(self) -> None:
        self._quit = True
        self._cancel_current.set()
        self._wake.set()
        self.wait(5000)

    # ---------------------------------------------------------------- the thread
    def _next(self) -> Job | None:
        with self._lock:
            for j in self._jobs:
                if j.status == "queued":
                    return j
        return None

    def run(self) -> None:
        while not self._quit:
            job = self._next()
            if job is None:
                self.idle.emit()
                self._wake.wait()
                self._wake.clear()
                continue
            self._current = job
            self._cancel_current.clear()
            s: Settings = self._settings_getter()
            self.log.emit(f"━━ {job.display_name}")
            self.job_changed.emit(job, "Starting…")
            last = [0.0]

            def progress(frac: float, msg: str, job=job) -> None:
                # at most ~10 GUI updates a second
                import time
                now = time.monotonic()
                if now - last[0] > 0.1 or frac >= 1.0:
                    last[0] = now
                    self.job_changed.emit(job, msg)

            run_job(job, s, log=self.log.emit, progress=progress, cancelled=self._cancel_current.is_set)
            self._current = None
            self.job_changed.emit(job, "")


class LiveController(QObject):
    caption = Signal(object)        # Caption
    status = Signal(str)
    level = Signal(float)
    error = Signal(str)
    running_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lt: LiveTranslator | None = None

    @property
    def running(self) -> bool:
        return self._lt is not None

    def start(self, s: Settings) -> None:
        if self._lt is not None:
            return
        self._lt = LiveTranslator(s, on_caption=self.caption.emit, on_status=self.status.emit,
                                  on_level=self.level.emit, on_error=self._on_error)
        self._lt.start()
        self.running_changed.emit(True)

    def _on_error(self, msg: str) -> None:
        self.error.emit(msg)

    def stop(self) -> None:
        lt, self._lt = self._lt, None
        if lt is not None:
            threading.Thread(target=lt.stop, daemon=True).start()     # never block the GUI on model teardown
        self.running_changed.emit(False)


__all__ = ["JobQueue", "LiveController", "Caption"]
