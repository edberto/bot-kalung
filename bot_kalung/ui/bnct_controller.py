"""Drives BNCT polling from the UI (PRD Section 15).

A `QTimer` fires every interval (default 5 min, configurable). Each tick spawns
one worker thread that only does the network fetch — SQLite work and Qt signals
stay on the main thread. Results are turned into per-shipment readings, stored,
and any transition notifications are emitted for the tray and the detail view.

Runs only while the app is open (the chosen model). Failures never raise: a bad
poll logs a message and the timer keeps going.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from ..services import bnct
from ..services.bnct_monitor import BnctMonitor, Notification, build_reading
from ..services.containers import Containers
from ..services.notifications import NotificationStore
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


class _ContainerWorker(QThread):
    """Fetch several containers from the BNCT container search in one session."""

    done = pyqtSignal(object, object)   # ({no: [cards]} | None, error | None)

    def __init__(self, client, numbers, parent=None):
        super().__init__(parent)
        self._client = client
        self._numbers = numbers

    def run(self):
        try:
            self.done.emit(self._client.fetch_containers_batch(self._numbers), None)
        except bnct.BnctError as exc:
            self.done.emit(None, str(exc))
        except Exception as exc:  # noqa: BLE001 - a poll must never crash the app
            self.done.emit(None, f"Kesalahan saat memantau kontainer BNCT: {exc}")


class BnctController(QObject):
    notified = pyqtSignal(object)       # Notification
    checked = pyqtSignal(str)           # shipment_id whose latest check changed
    vessel_checked = pyqtSignal(str)    # monitored-vessel id whose check changed
    container_checked = pyqtSignal(str)  # shipment_id whose container status changed
    polled = pyqtSignal()               # a poll cycle completed (any result)
    error = pyqtSignal(str)

    def __init__(self, db, settings, client=None, parent=None):
        super().__init__(parent)
        self.monitor = BnctMonitor(db)
        self.vessels = VesselMonitor(db)
        self.containers = Containers(db)
        self.notifications = NotificationStore(db)
        self.settings = settings
        self.client = client or bnct.HttpBnctClient()
        self._worker: _FetchWorker | None = None
        self._container_worker: _ContainerWorker | None = None
        self._pending_container_rows: list = []

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
        if self._container_worker is not None and self._container_worker.isRunning():
            self._container_worker.wait(20_000)

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
        self._poll_containers(vessels)
        self.polled.emit()

    # -- container tracking ------------------------------------------------

    def _poll_containers(self, vessels):
        """Fetch BNCT status for every container on an active shipment whose
        vessel is currently on the schedule and not yet departing — 51-STACK
        RECEIVING only happens in that window, so this bounds the request volume.
        Runs in its own worker; skips if the previous batch is still in flight.
        """
        if self._container_worker is not None and self._container_worker.isRunning():
            return
        rows, watchable = [], {}
        for row in self.containers.active_with_vessel():
            key = (row["vessel_name"] or "", row["voyage"] or "")
            if key not in watchable:
                reading = bnct.read_for_shipment(vessels, key[0], key[1])
                watchable[key] = reading.found and not reading.departing
            if watchable[key]:
                rows.append(row)
        if not rows:
            return
        self._pending_container_rows = rows
        numbers = sorted({r["container_no"] for r in rows})
        self._container_worker = _ContainerWorker(self.client, numbers, self)
        self._container_worker.done.connect(self._on_containers_fetched)
        self._container_worker.start()

    def _on_containers_fetched(self, results, error):
        if error is not None or not results:
            return
        stamp = datetime.now().isoformat(timespec="seconds")
        for row in self._pending_container_rows:
            cards = results.get(row["container_no"], [])
            card = bnct.match_container(
                cards, row["vessel_name"] or "", row["voyage"] or "")
            if card is None:
                continue
            previous = self.containers.update_status(
                row["id"], site=card.site, status_code=card.status_code,
                status_text=card.status_text, type=card.type, checked_at=stamp)
            self.container_checked.emit(row["shipment_id"])
            # Notify only on the transition INTO 51, so it fires once.
            if (previous != bnct.STACK_RECEIVING_CODE and card.at_stack_receiving):
                label = f"{row['exporter_code']}{row['sequence_number']}"
                title = f"{label}: {row['container_no']} STACK RECEIVING"
                body = (f"Kontainer {row['container_no']} sedang diterima di stack "
                        f"BNCT ({card.site}). Kapal {row['vessel_name']} "
                        f"{row['voyage'] or ''}.".rstrip())
                self.notifications.add("container", row["shipment_id"], title, body,
                                       created_at=stamp)
                self.notified.emit(Notification(
                    "container", row["shipment_id"], title, body))
