from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    started = Signal()
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(Exception)


class FunctionWorker(QRunnable):
    """Run a blocking callable on a Qt thread-pool and report the result."""

    def __init__(
        self,
        function: Callable[..., Any],
        *args: Any,
        progress_kwarg: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.progress_kwarg = progress_kwarg
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        self.signals.started.emit()
        try:
            if self.progress_kwarg is not None:
                self.kwargs[self.progress_kwarg] = self.signals.progress.emit
            result = self.function(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(exc)
            return
        self.signals.finished.emit(result)
