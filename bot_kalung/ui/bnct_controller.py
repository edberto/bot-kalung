"""Drives BNCT polling from the UI (PRD Section 15).

A `QTimer` fires every interval (default 5 min, configurable). Each tick spawns
one worker thread that only does the network fetch — SQLite work and Qt signals
stay on the main thread. Results are turned into per-shipment readings, stored,
and any transition notifications are emitted for the tray and the detail view.

Runs only while the app is open (the chosen model). Failures never raise: a bad
poll logs a message and the timer keeps going.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from ..services import bnct
from ..services.bnct_monitor import BnctMonitor, build_reading
from ..services.vessel_monitor import VesselMonitor

DEFAULT_INTERVAL_MINUTES = 5
MIN_INTERVAL_MINUTES = 1


def interval_minutes(settings) -> int:
    """Configured poll interval, clamped to a sane floor."""
    try:
        value = int(settings.get("bnct_interval_minutes", DEFAULT_INTERVAL_MINUTES))
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_MINUTES
    return max(MIN_INTERVAL_MINUTES, value)


class _FetchWorker(QThread):
    """Network fetch only; hands back the parsed vessel list or an error."""

    done = pyqtSignal(object, object)   # (vessels | None, error_message | None)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self._client = client

    def run(self):
        try:
            self.done.emit(self._client.fetch_vessels(), None)
        except bnct.BnctError as exc:
            self.done.emit(None, str(exc))
        except Exception as exc:  # noqa: BLE001 - a poll must never crash the app
            self.done.emit(None, f"Kesalahan tak terduga saat memantau BNCT: {exc}")


class BnctController(QObject):
    notified = pyqtSignal(object)       # Notification
    checked = pyqtSignal(str)           # shipment_id whose latest check changed
    vessel_checked = pyqtSignal(str)    # monitored-vessel id whose check changed
    polled = pyqtSignal()               # a poll cycle completed (any result)
    error = pyqtSignal(str)

    def __init__(self, db, settings, client=None, parent=None):
        super().__init__(parent)
        self.monitor = BnctMonitor(db)
        self.vessels = VesselMonitor(db)
        self.settings = settings
        self.client = client or bnct.HttpBnctClient()
        self._worker: _FetchWorker | None = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_now)

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        self.timer.start(interval_minutes(self.settings) * 60_000)
        self.poll_now()          # don't wait a full interval for the first read

    def apply_interval(self):
        """Re-read the interval after the user edits it in Settings."""
        if self.timer.isActive():
            self.timer.start(interval_minutes(self.settings) * 60_000)

    def stop(self):
        self.timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(20_000)

    # -- polling -----------------------------------------------------------

    def poll_now(self):
        if self._worker is not None and self._worker.isRunning():
            return               # a previous fetch is still in flight; skip
        if not self.monitor.monitored() and not self.vessels.monitored():
            self.polled.emit()   # nothing to watch — no need to hit the portal
            return
        self._worker = _FetchWorker(self.client, self)
        self._worker.done.connect(self._on_fetched)
        self._worker.start()

    def _on_fetched(self, vessels, error):
        if error is not None:
            self.error.emit(error)
            self.polled.emit()
            return
        # Shipments and standalone vessels share this one fetch.
        for row in self.monitor.monitored():
            reading = build_reading(row, vessels)
            label = f"{row['exporter_code']}{row['sequence_number']}"
            for note in self.monitor.process(row["id"], label, reading):
                self.notified.emit(note)
            self.checked.emit(row["id"])
        for row in self.vessels.monitored():
            reading = bnct.read_for_shipment(
                vessels, row["vessel_name"] or "", row["voyage"] or "")
            for note in self.vessels.process(row["id"], reading):
                self.notified.emit(note)
            self.vessel_checked.emit(row["id"])
        self.polled.emit()
