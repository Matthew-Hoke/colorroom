"""Background work, so the window never freezes while an image is analyzed.

This replaces the Web Worker of the browser version. A task runs a plain function on a
thread from Qt's pool and reports back to the window through signals.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from ..core.index import AnalysisCancelled


class TaskSignals(QObject):
    finished = Signal(int, object)  # task id, result
    failed = Signal(int, object)  # task id, the exception (None when cancelled)
    progress = Signal(int, float, str)  # task id, fraction 0..1, message


class Task(QRunnable):
    """Runs `work(progress, cancelled)` off the main thread.

    `work` receives two callables: `progress(fraction, message)` to report how far it
    is, and `cancelled()` which becomes True after `cancel()` was called.
    """

    _PROGRESS_INTERVAL = 0.04  # seconds between progress signals, to keep the UI light

    def __init__(self, task_id: int, work: Callable) -> None:
        super().__init__()
        self.task_id = task_id
        self.signals = TaskSignals()
        self._work = work
        self._cancel = threading.Event()
        self._last_report = 0.0
        self.setAutoDelete(False)  # the window keeps a reference until the task reports back

    def cancel(self) -> None:
        self._cancel.set()

    def _progress(self, fraction: float, message: str) -> None:
        now = time.monotonic()
        if fraction >= 1.0 or now - self._last_report >= self._PROGRESS_INTERVAL:
            self._last_report = now
            self.signals.progress.emit(self.task_id, float(fraction), message)

    def run(self) -> None:
        try:
            result = self._work(self._progress, self._cancel.is_set)
        except AnalysisCancelled:
            self.signals.failed.emit(self.task_id, None)
        except Exception as error:  # reported to the user by the window
            self.signals.failed.emit(self.task_id, error)
        else:
            self.signals.finished.emit(self.task_id, result)
