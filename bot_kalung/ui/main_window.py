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
from ..services.shipments import Shipments
from .bnct_controller import BnctController
from .dashboard import DashboardView
from .history import HistoryView
from .new_shipment import NewShipmentWizard
from .settings_view import SettingsView
from .shipment_detail import ShipmentDetailView
from .sidebar import Sidebar


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


class MainWindow(QMainWindow):
    theme_changed = pyqtSignal(str)

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.shipments = Shipments(ctx.db)
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
        self.sidebar.settings_clicked.connect(self.open_settings)
        self.sidebar.home_clicked.connect(self.open_dashboard)
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet(theme.style("background: #ffffff;"))

        self.dashboard = DashboardView()
        self.dashboard.new_shipment_clicked.connect(self.open_wizard)
        self.dashboard.shipment_opened.connect(self.open_shipment)
        self.stack.addWidget(self.dashboard)

        self.wizard = NewShipmentWizard(ctx)
        # The wizard's own Batal button already confirms, so skip the guard.
        self.wizard.cancelled.connect(self._on_wizard_cancelled)
        self.wizard.created.connect(self._on_shipment_created)
        self.wizard.open_folder_requested.connect(open_in_explorer)
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

        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        # -- BNCT monitoring (PRD 15) --------------------------------------
        self.tray = self._build_tray()
        self.bnct = BnctController(ctx.db, ctx.settings)
        self.bnct.notified.connect(self._on_bnct_notification)
        self.bnct.checked.connect(self.detail.refresh_bnct)
        self.settings.saved.connect(self.bnct.apply_interval)

        self.refresh()
        # Don't reach out to the live BNCT portal under the offscreen platform
        # (tests / headless); real runs start polling normally.
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            self.bnct.start()

    # -- BNCT ------------------------------------------------------------

    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self)
        icon = self.windowIcon()
        tray.setIcon(icon if not icon.isNull() else QIcon())
        tray.setToolTip(APP_NAME)
        tray.show()
        return tray

    def _bnct_poll_now(self):
        self.bnct.poll_now()
        self.detail.message.show_info("Memeriksa BNCT...")

    def _on_bnct_notification(self, note):
        """A monitoring transition — notify natively, and via a dialog for the
        departure alert since that one demands action (pay LOLO).
        """
        if self.tray is not None:
            icon = (QSystemTrayIcon.MessageIcon.Critical
                    if note.kind == "departing"
                    else QSystemTrayIcon.MessageIcon.Information)
            self.tray.showMessage(note.title, note.body, icon, 15_000)
        if note.kind == "departing":
            QMessageBox.warning(self, note.title, note.body)

    # -- navigation ------------------------------------------------------

    def _leave_wizard_ok(self) -> bool:
        """PRD 2.3 — confirm before discarding an in-progress wizard."""
        if self.stack.currentIndex() != VIEW_WIZARD:
            return True
        answer = QMessageBox.question(
            self, "Batalkan pengaturan pengiriman?",
            "Pengaturan pengiriman yang belum disimpan akan hilang. Lanjutkan?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

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
        self._go(VIEW_WIZARD)

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

    def open_shipment(self, shipment_id: str):
        if not self._leave_wizard_ok():
            return
        self.current_shipment_id = shipment_id
        self.sidebar.select_shipment(shipment_id)
        self.detail.load(shipment_id)
        self._go(VIEW_DETAIL)

    def open_history(self):
        if not self._leave_wizard_ok():
            return
        self.sidebar.select_shipment(None)
        self.history.refresh()
        self._go(VIEW_HISTORY)

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
        self.detail.shutdown()
        self.wizard.shutdown()
        self.settings.shutdown()
        super().closeEvent(event)

    def refresh(self):
        active = self.shipments.active()
        self.sidebar.refresh(active, self.shipments.progress)
        self.dashboard.refresh(
            active, self.shipments.progress,
            overdue=self.shipments.count_overdue_steps(),
            this_month=self.shipments.count_completed_this_month())
