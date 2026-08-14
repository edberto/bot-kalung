"""Drives the folder-scan tracker from the UI.

Mirrors BnctController: a `QTimer` fires every interval (default 5 min); each
tick runs one worker thread that walks the Drive, reads new shipments' workbooks
and writes them to the database, then signals the result back to the main thread.
The worker does the slow work (Drive I/O + Excel COM) off the UI thread and
initialises COM for xlwings. Failures never raise — a bad scan signals an error
and the timer keeps going.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from ..services import tracker

DEFAULT_INTERVAL_MINUTES = 5
MIN_INTERVAL_MINUTES = 1


def interval_minutes(settings) -> int:
    try:
        value = int(settings.get("scan_interval_minutes", DEFAULT_INTERVAL_MINUTES))
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_MINUTES
    return max(MIN_INTERVAL_MINUTES, value)


class _ScanWorker(QThread):
    """One full scan: Drive walk + Excel reads + database writes."""

    done = pyqtSignal(object, object)   # (ScanApplyResult | None, error | None)

    def __init__(self, db, drive_root, settings, parent=None):
        super().__init__(parent)
        self._db = db
        self._drive_root = drive_root
        self._settings = settings

    def run(self):
        # xlwings talks to Excel over COM, which must be initialised per thread.
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:      # noqa: BLE001 - not fatal; the read may still work
            pythoncom = None
        try:
            result = tracker.run_scan(self._db, self._drive_root,
                                      settings=self._settings)
            self.done.emit(result, None)
        except Exception as exc:  # noqa: BLE001 - a scan must never crash the app
            self.done.emit(None, f"Kesalahan saat memindai folder: {exc}")
        finally:
            if pythoncom is not None:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass


class ScanController(QObject):
    scanned = pyqtSignal(object)    # ScanApplyResult
    started = pyqtSignal()          # a scan began
    error = pyqtSignal(str)

    def __init__(self, db, settings, drive_root, parent=None):
        super().__init__(parent)
        self.db = db
        self.settings = settings
        self.drive_root = drive_root
        self._worker: _ScanWorker | None = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.scan_now)

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        self.timer.start(interval_minutes(self.settings) * 60_000)
        self.scan_now()          # scan on launch, don't wait a full interval

    def apply_interval(self):
        if self.timer.isActive():
            self.timer.start(interval_minutes(self.settings) * 60_000)

    def stop(self):
        self.timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(30_000)

    # -- scanning ----------------------------------------------------------

    @property
    def scanning(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def scan_now(self):
        if self.scanning:
            return               # a previous scan is still running; skip
        self.started.emit()
        self._worker = _ScanWorker(self.db, self.drive_root, self.settings, self)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, result, error):
        if error is not None:
            self.error.emit(error)
            return
        self.scanned.emit(result)
