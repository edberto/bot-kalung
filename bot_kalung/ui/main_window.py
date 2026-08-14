"""Main window: sidebar + content stack (PRD Sections 2.1 - 2.3)."""

from __future__ import annotations

from . import theme

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget, QSystemTrayIcon,
    QWidget,
)

import os
import subprocess
from pathlib import Path

from ..core.constants import APP_NAME
from ..core.context import AppContext
from ..services.action_items import ActionItems
from ..services.shipments import Shipments
from .bnct_controller import BnctController
from .dashboard import DashboardView
from .history import HistoryView
from ..services.notifications import NotificationStore
from .scan_controller import ScanController
from .scan_view import ScanView
from .all_shipments import AllShipmentsView
from .audit_view import AuditView
from .notifications_view import NotificationsView
from .resequence_dialog import ResequenceDialog
from .settings_view import SettingsView
from .shipment_detail import ShipmentDetailView
from .sidebar import Sidebar
from .vessel_monitor_view import VesselMonitorView


def open_in_explorer(path: str) -> bool:
    """Reveal a folder in Windows Explorer (PRD Sections 5, 6.1, 12)."""
    target = Path(path)
    if not target.exists():
        return False
    try:
        os.startfile(str(target))  # noqa: S606 - Windows-only by design
        return True
    except (AttributeError, OSError):
        try:
            subprocess.Popen(["explorer", str(target)])
            return True
        except OSError:
            return False

# Stack indices, kept as names so the routing reads clearly.
VIEW_DASHBOARD = 0
VIEW_WIZARD = 1
VIEW_DETAIL = 2
VIEW_HISTORY = 3
VIEW_SETTINGS = 4
VIEW_NOTIFICATIONS = 5
VIEW_AUDIT = 6
VIEW_ALL_SHIPMENTS = 7
VIEW_VESSEL_MONITOR = 8


class MainWindow(QMainWindow):
    theme_changed = pyqtSignal(str)

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.shipments = Shipments(ctx.db)
        self.action_items = ActionItems(ctx.db)
        self.current_shipment_id: str | None = None
        self.previous_view = VIEW_DASHBOARD

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1100, 700)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.new_shipment_clicked.connect(self.open_wizard)
        self.sidebar.shipment_selected.connect(self.open_shipment)
        self.sidebar.history_clicked.connect(self.open_history)
        self.sidebar.all_shipments_clicked.connect(self.open_all_shipments)
        self.sidebar.settings_clicked.connect(self.open_settings)
        self.sidebar.home_clicked.connect(self.open_dashboard)
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet(theme.style("background: #ffffff;"))

        self.dashboard = DashboardView()
        self.dashboard.new_shipment_clicked.connect(self.open_wizard)
        self.dashboard.shipment_opened.connect(self.open_shipment)
        self.dashboard.resequence_requested.connect(self.open_resequence)
        self.dashboard.calendar_entry_clicked.connect(self._on_calendar_entry)
        self.dashboard.calendar_range_requested.connect(self._load_calendar)
        self.stack.addWidget(self.dashboard)

        # Placeholder for the folder-scan screen (built in a later phase). Named
        # `wizard` for now so the view slot and teardown hooks stay stable.
        self.wizard = ScanView()
        self.stack.addWidget(self.wizard)

        self.detail = ShipmentDetailView(ctx.db, ctx.settings)
        self.detail.changed.connect(self.refresh)
        self.detail.completed.connect(self._on_shipment_completed)
        self.detail.deleted.connect(self._on_shipment_deleted)
        self.detail.bnct_refresh_requested.connect(self._bnct_poll_now)
        self.stack.addWidget(self.detail)

        self.history = HistoryView(ctx.db)
        self.history.open_folder_requested.connect(open_in_explorer)
        self.history.changed.connect(self.refresh)
        self.stack.addWidget(self.history)

        self.settings = SettingsView(ctx)
        self.settings.back_requested.connect(self.go_back)
        self.settings.saved.connect(self.refresh)
        self.settings.theme_changed.connect(self.theme_changed)
        self.stack.addWidget(self.settings)

        self.notifications = NotificationsView(ctx.db)
        self.notifications.open_shipment_requested.connect(self.open_shipment)
        self.notifications.open_vessel_monitor_requested.connect(
            self.open_vessel_monitor)
        self.notifications.changed.connect(self._refresh_notification_badge)
        self.stack.addWidget(self.notifications)

        self.audit = AuditView(ctx.db)
        self.stack.addWidget(self.audit)

        self.all_shipments = AllShipmentsView(ctx.db)
        self.all_shipments.shipment_opened.connect(self.open_shipment)
        self.stack.addWidget(self.all_shipments)

        self.vessel_monitor = VesselMonitorView(ctx.db)
        self.vessel_monitor.vessel_added.connect(self._on_vessel_added)
        self.stack.addWidget(self.vessel_monitor)

        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.sidebar.notifications_clicked.connect(self.open_notifications)
        self.sidebar.audit_clicked.connect(self.open_audit)
        self.sidebar.monitor_clicked.connect(self.open_vessel_monitor)

        # -- BNCT monitoring (PRD 15) --------------------------------------
        # Clicking a tray toast can only tell us *a* message was clicked, not
        # which one, so remember the shipment behind the most recent toast.
        self._notified_shipment: str | None = None
        self._bnct_manual_check = False    # true between a "Periksa" click and its result
        self._bnct_error: str | None = None
        self.notification_store = NotificationStore(ctx.db)
        self.tray = self._build_tray()
        self.bnct = BnctController(ctx.db, ctx.settings)
        self.bnct.notified.connect(self._on_bnct_notification)
        self.bnct.checked.connect(self.detail.refresh_bnct)
        self.bnct.error.connect(self._on_bnct_error)
        self.bnct.polled.connect(self._on_bnct_polled)
        self.settings.saved.connect(self.bnct.apply_interval)

        # -- folder-scan tracker -------------------------------------------
        # Discovers active shipments by scanning the Drive on a timer; the
        # ScanView drives a manual scan and shows the report.
        self.scan = ScanController(ctx.db, ctx.settings, ctx.drive_root)
        self.wizard.scan_requested.connect(self.scan.scan_now)
        self.scan.started.connect(lambda: self.wizard.set_scanning(True))
        self.scan.scanned.connect(self._on_scanned)
        self.scan.error.connect(self.wizard.show_error)
        self.settings.saved.connect(self.scan.apply_interval)

        # Whether this process may reach the live BNCT portal / Drive. Offscreen
        # means tests/headless, where a network call or Drive walk would hang.
        self._bnct_live = os.environ.get("QT_QPA_PLATFORM") != "offscreen"

        self.refresh()
        self._refresh_notification_badge()
        if self._bnct_live:
            self.bnct.start()
            self.scan.start()

    # -- BNCT ------------------------------------------------------------

    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self)
        icon = self.windowIcon()
        tray.setIcon(icon if not icon.isNull() else QIcon())
        tray.setToolTip(APP_NAME)
        tray.messageClicked.connect(self._open_notified_shipment)
        tray.show()
        return tray

    def _refresh_notification_badge(self):
        self.sidebar.set_notification_count(
            self.notification_store.unread_count())

    def _bnct_poll_now(self):
        self._bnct_manual_check = True
        self._bnct_error = None
        self.detail.message.show_info("Memeriksa BNCT...")
        self.bnct.poll_now()

    def _on_bnct_error(self, message: str):
        # Remember it; the banner is resolved when the poll cycle completes.
        self._bnct_error = message

    def _on_bnct_polled(self):
        """A poll cycle finished. Refresh the vessel screen if it is showing, and
        only touch the banner for a manual check, so the 5-minute background
        polls never clear an unrelated message.
        """
        if self.stack.currentIndex() == VIEW_VESSEL_MONITOR:
            self.vessel_monitor.refresh()
        if not self._bnct_manual_check:
            return
        self._bnct_manual_check = False
        if self._bnct_error:
            self.detail.message.show_error(self._bnct_error)
        else:
            self.detail.message.show_success("Pemeriksaan BNCT selesai.")

    def _on_bnct_notification(self, note):
        """A monitoring transition — notify natively, push to ntfy, and raise a
        dialog for the departure alert since that one demands action (pay LOLO).
        A note with no shipment_id is a standalone vessel; those route to the
        Monitor Kapal screen instead of a shipment.
        """
        self._notified_shipment = note.shipment_id
        self._push_ntfy(note)                    # additional delivery channel
        # The notification was already persisted by the monitor; reflect it in
        # the sidebar counter and the list if it is on screen.
        self._refresh_notification_badge()
        if self.stack.currentIndex() == VIEW_NOTIFICATIONS:
            self.notifications.refresh()
        if self.stack.currentIndex() == VIEW_VESSEL_MONITOR:
            self.vessel_monitor.refresh()
        if self.tray is not None:
            icon = (QSystemTrayIcon.MessageIcon.Critical
                    if note.kind == "departing"
                    else QSystemTrayIcon.MessageIcon.Information)
            self.tray.showMessage(note.title, note.body, icon, 15_000)
        if note.kind == "departing":
            box = QMessageBox(QMessageBox.Icon.Warning, note.title, note.body,
                              parent=self)
            open_label = "Buka Pengiriman" if note.shipment_id else "Buka Monitor Kapal"
            open_btn = box.addButton(open_label, QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Tutup", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is open_btn:
                if note.shipment_id:
                    self._focus_shipment(note.shipment_id)
                else:
                    self._focus_vessel_monitor()

    def _push_ntfy(self, note):
        """Fire an ntfy push on a throwaway thread so the UI never blocks."""
        if not self.ctx.settings.get_bool("ntfy_enabled"):
            return
        import threading

        from ..services import ntfy
        threading.Thread(
            target=ntfy.publish,
            args=(self.ctx.settings, note.title, note.body),
            kwargs={"kind": note.kind}, daemon=True).start()

    def _open_notified_shipment(self):
        """Tray toast was clicked — jump to whatever it was about."""
        if self._notified_shipment:
            self._focus_shipment(self._notified_shipment)
        else:
            self._focus_vessel_monitor()

    def _focus_vessel_monitor(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.open_vessel_monitor()

    def _focus_shipment(self, shipment_id: str | None):
        if not shipment_id or self.shipments.get(shipment_id) is None:
            return          # deleted or unknown; nothing to open
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.open_shipment(shipment_id)

    # -- navigation ------------------------------------------------------

    def _leave_wizard_ok(self) -> bool:
        # The creation wizard is gone; the scan placeholder has nothing to lose,
        # so leaving is always fine. Retained because every open_* still calls it.
        return True

    def _go(self, index: int):
        if index != self.stack.currentIndex():
            self.previous_view = self.stack.currentIndex()
        self.stack.setCurrentIndex(index)

    def open_dashboard(self):
        if not self._leave_wizard_ok():
            return
        self.current_shipment_id = None
        self.sidebar.select_shipment(None)
        self.refresh()
        self._go(VIEW_DASHBOARD)

    def open_wizard(self):
        self.current_shipment_id = None
        self.sidebar.select_shipment(None)
        # The scan screen is long-lived, so clear the previous run before showing.
        self.wizard.reset()
        self._go(VIEW_WIZARD)

    def _on_scanned(self, result):
        """A folder scan finished — show its report and refresh the shipment set."""
        self.wizard.show_result(result)
        self.refresh()
        if self.stack.currentIndex() == VIEW_ALL_SHIPMENTS:
            self.all_shipments.refresh()

    def _on_shipment_completed(self, label: str):
        """PRD 6.4 — completing a shipment returns to the dashboard."""
        self.current_shipment_id = None
        self.sidebar.select_shipment(None)
        self.refresh()
        self._go(VIEW_DASHBOARD)
        QMessageBox.information(
            self, APP_NAME, f"Pengiriman {label} ditandai selesai.")

    def _on_shipment_deleted(self, note: str):
        """A deleted shipment returns to the dashboard, like completion does."""
        self.current_shipment_id = None
        self.sidebar.select_shipment(None)
        self.refresh()
        self._go(VIEW_DASHBOARD)
        QMessageBox.information(self, APP_NAME, note)

    def _on_wizard_cancelled(self):
        self.current_shipment_id = None
        self.sidebar.select_shipment(None)
        self.refresh()
        self._go(VIEW_DASHBOARD)

    def _on_shipment_created(self, shipment_id: str):
        """PRD 2.3 — wizard success routes to the new shipment's detail view."""
        self.refresh()
        self.current_shipment_id = shipment_id
        self.sidebar.select_shipment(shipment_id)
        self.detail.load(shipment_id)
        self._go(VIEW_DETAIL)
        # Check BNCT straight away rather than waiting up to a full interval —
        # a new shipment's vessel is often already on the schedule.
        if self._bnct_live:
            self.bnct.poll_now()

    def open_shipment(self, shipment_id: str):
        if not self._leave_wizard_ok():
            return
        self.current_shipment_id = shipment_id
        self.sidebar.select_shipment(shipment_id)
        self.detail.load(shipment_id)
        self._go(VIEW_DETAIL)

    def open_all_shipments(self):
        if not self._leave_wizard_ok():
            return
        self.sidebar.select_shipment(None)
        self.all_shipments.refresh()
        self._go(VIEW_ALL_SHIPMENTS)

    def open_history(self):
        if not self._leave_wizard_ok():
            return
        self.sidebar.select_shipment(None)
        self.history.refresh()
        self._go(VIEW_HISTORY)

    def open_notifications(self):
        if not self._leave_wizard_ok():
            return
        self.sidebar.select_shipment(None)
        self.notifications.refresh()
        self._go(VIEW_NOTIFICATIONS)

    def _load_calendar(self, start_iso: str, end_iso: str):
        """The calendar asked for a month's entries."""
        self.dashboard.calendar.set_entries(
            self.shipments.calendar_entries(start_iso, end_iso))

    def _on_calendar_entry(self, shipment_id: str, item_id: str):
        """Open the shipment a calendar card belongs to, focusing its action item."""
        if self.shipments.get(shipment_id) is None:
            return
        self.open_shipment(shipment_id)
        if item_id:
            self.detail.focus_item(item_id)

    def open_resequence(self):
        """Change a shipment's sequence number (folder, files, Excel, PDFs)."""
        if not self._leave_wizard_ok():
            return
        dialog = ResequenceDialog(self.ctx.db, self)
        dialog.exec()
        self.refresh()   # numbers and folder paths may have changed

    def open_audit(self):
        if not self._leave_wizard_ok():
            return
        self.sidebar.select_shipment(None)
        self.audit.refresh()
        self._go(VIEW_AUDIT)

    def open_vessel_monitor(self):
        if not self._leave_wizard_ok():
            return
        self.sidebar.select_shipment(None)
        self.vessel_monitor.refresh()
        self._go(VIEW_VESSEL_MONITOR)

    def _on_vessel_added(self):
        """A vessel was just added — check it right away, like a new shipment."""
        if self._bnct_live:
            self.bnct.poll_now()

    def open_settings(self):
        if not self._leave_wizard_ok():
            return
        self.sidebar.select_shipment(None)
        self.settings.load()   # reflect anything changed since last opened
        self._go(VIEW_SETTINGS)

    def go_back(self):
        """PRD 2.3 — Settings "Back" returns to the previous view."""
        self._go(self.previous_view)

    # -- data ------------------------------------------------------------

    def closeEvent(self, event):
        # Let in-flight extraction / permit / connection checks finish so no
        # file stays locked and no worker outlives its receiver.
        self.bnct.stop()
        self.scan.stop()
        self.detail.shutdown()
        self.wizard.shutdown()
        self.settings.shutdown()
        super().closeEvent(event)

    def refresh(self):
        active = self.shipments.active()
        self.sidebar.refresh(active, self.action_items.progress)
        self.dashboard.refresh(
            active, self.action_items.progress,
            overdue=self.action_items.count_overdue(),
            this_month=self.shipments.count_completed_this_month())
        # A deleted shipment cascades its notifications away, so keep the badge
        # in sync with any change to the shipment set.
        if hasattr(self, "notification_store"):
            self._refresh_notification_badge()
