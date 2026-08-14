"""Drives BNCT polling from the UI (PRD Section 15).

A `QTimer` fires every interval (default 5 min, configurable). Each tick spawns
one worker thread that does the WHOLE cycle off the UI thread: fetch the vessel
list, store per-shipment/vessel readings, fetch and store container statuses, and
collect any transition notifications. Only the collected results cross back to
the main thread, which just emits signals — no network or SQLite runs on the UI
thread, so the app never freezes on a poll (the DB lives on the Google Drive
network drive, where writes are slow).

Runs only while the app is open. Failures never raise: a bad poll reports a
message and the timer keeps going.
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


class _PollWorker(QThread):
    """One full BNCT poll off the UI thread: fetch + store + collect results.

    All SQLite work runs here. `Database.cursor()` opens a fresh connection per
    call, so the monitor/vessel/container services are safe to use from this
    thread even though they were constructed on the main thread.
    """

    done = pyqtSignal(object, object)   # (result dict | None, error | None)

    def __init__(self, client, monitor, vessels, containers, notifications,
                 parent=None):
        super().__init__(parent)
        self._client = client
        self._monitor = monitor
        self._vessels = vessels
        self._containers = containers
        self._notifications = notifications

    def run(self):
        try:
            vessels = self._client.fetch_vessels()
        except bnct.BnctError as exc:
            self.done.emit(None, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a poll must never crash the app
            self.done.emit(None, f"Kesalahan tak terduga saat memantau BNCT: {exc}")
            return

        result = {"notifications": [], "checked": [], "vessel_checked": [],
                  "container_checked": []}
        try:
            self._process(vessels, result)
        except Exception:  # noqa: BLE001 - keep whatever was collected so far
            pass
        self.done.emit(result, None)

    def _process(self, vessels, result):
        for row in self._monitor.monitored():
            reading = build_reading(row, vessels)
            label = f"{row['exporter_code']}{row['sequence_number']}"
            for note in self._monitor.process(row["id"], label, reading):
                result["notifications"].append(note)
            result["checked"].append(row["id"])

        for row in self._vessels.monitored():
            reading = bnct.read_for_shipment(
                vessels, row["vessel_name"] or "", row["voyage"] or "")
            for note in self._vessels.process(row["id"], reading):
                result["notifications"].append(note)
            result["vessel_checked"].append(row["id"])

        self._process_containers(vessels, result)

    def _process_containers(self, vessels, result):
        """Poll containers only for active shipments whose vessel is on the
        schedule and not departing — 51-STACK RECEIVING only happens then, which
        also bounds the request volume.
        """
        rows, watchable = [], {}
        for row in self._containers.active_with_vessel():
            key = (row["vessel_name"] or "", row["voyage"] or "")
            if key not in watchable:
                reading = bnct.read_for_shipment(vessels, key[0], key[1])
                watchable[key] = reading.found and not reading.departing
            if watchable[key]:
                rows.append(row)
        if not rows:
            return

        numbers = sorted({r["container_no"] for r in rows})
        try:
            cards_by_no = self._client.fetch_containers_batch(numbers)
        except Exception:  # noqa: BLE001 - container polling is best-effort
            return

        stamp = datetime.now().isoformat(timespec="seconds")
        for row in rows:
            card = bnct.match_container(
                cards_by_no.get(row["container_no"], []),
                row["vessel_name"] or "", row["voyage"] or "")
            if card is None:
                continue
            previous = self._containers.update_status(
                row["id"], site=card.site, status_code=card.status_code,
                status_text=card.status_text, type=card.type, checked_at=stamp)
            result["container_checked"].append(row["shipment_id"])
            # Notify only on the transition INTO 51, so it fires once.
            if previous != bnct.STACK_RECEIVING_CODE and card.at_stack_receiving:
                label = f"{row['exporter_code']}{row['sequence_number']}"
                title = f"{label}: {row['container_no']} STACK RECEIVING"
                body = (f"Kontainer {row['container_no']} sedang diterima di stack "
                        f"BNCT ({card.site}). Kapal {row['vessel_name']} "
                        f"{row['voyage'] or ''}.".rstrip())
                self._notifications.add("container", row["shipment_id"], title,
                                        body, created_at=stamp)
                result["notifications"].append(Notification(
                    "container", row["shipment_id"], title, body))


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
        self._worker: _PollWorker | None = None

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
            self._worker.wait(30_000)

    # -- polling -----------------------------------------------------------

    def poll_now(self):
        if self._worker is not None and self._worker.isRunning():
            return               # a previous poll is still in flight; skip
        self._worker = _PollWorker(
            self.client, self.monitor, self.vessels, self.containers,
            self.notifications, self)
        self._worker.done.connect(self._on_polled)
        self._worker.start()

    def _on_polled(self, result, error):
        if error is not None:
            self.error.emit(error)
            self.polled.emit()
            return
        for note in result["notifications"]:
            self.notified.emit(note)
        for shipment_id in result["checked"]:
            self.checked.emit(shipment_id)
        for vessel_id in result["vessel_checked"]:
            self.vessel_checked.emit(vessel_id)
        for shipment_id in result["container_checked"]:
            self.container_checked.emit(shipment_id)
        self.polled.emit()
