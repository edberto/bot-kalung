"""Left navigation panel (PRD Section 2.4). Fixed 250 px."""

from __future__ import annotations

from . import theme

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout,
)

from ..core.constants import APP_NAME, EXPORTER_COLORS
from .widgets import Panel, PrimaryButton, days_until, format_date_id


def _see_all_button() -> QPushButton:
    """Link-styled 'see all' under the active list."""
    # Plain text, no arrow glyph — the font may not have one (the ◀/▶ month
    # arrows had to be replaced with drawn icons for exactly this reason).
    button = QPushButton("Lihat Semua")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(theme.style("""
        QPushButton { border: none; background: transparent; color: #2563eb;
                      font-size: 11px; text-align: left; padding: 2px 8px; }
        QPushButton:hover { color: #1d4ed8; text-decoration: underline; }
    """))
    return button


class ShipmentEntry(Panel):
    """One active shipment row: badge, vessel/voyage, ETD, progress."""

    def __init__(self, shipment, done: int, total: int):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Hover shows an outline, matching the dashboard cards. A transparent
        # 1px border in the normal state reserves the space so hovering does
        # not shift the layout. Background stays transparent so the item's
        # selection colour still shows through.
        self.setObjectName("shipmentEntry")
        self.setStyleSheet(theme.style(
            "#shipmentEntry { background: transparent;"
            " border: 1px solid transparent; border-radius: 6px; }"
            "#shipmentEntry:hover { background: #f8fafc;"
            " border: 1px solid #93c5fd; }"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(6)
        code = shipment["exporter_code"]
        badge = QLabel(f"{code}{shipment['sequence_number']}")
        badge.setStyleSheet(theme.style(
            f"background: {EXPORTER_COLORS.get(code, '#6b7280')}; color: white;"
            "border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: 700;"))
        top.addWidget(badge)
        top.addStretch(1)
        layout.addLayout(top)

        vessel = f"{shipment['vessel_name'] or ''} {shipment['voyage'] or ''}".strip()
        vessel_label = QLabel(vessel or "(tanpa kapal)")
        # Wrap rather than clip: the sidebar is only 250px, and long vessel +
        # voyage names (e.g. "EVER CONCERT 0798-087N") would otherwise be cut.
        vessel_label.setWordWrap(True)
        vessel_label.setStyleSheet(theme.style("font-size: 12px; font-weight: 600; color: #111827;"))
        layout.addWidget(vessel_label)

        # Same wording and urgency rule as the dashboard cards (PRD 4.1).
        remaining = days_until(shipment["etd_belawan"])
        urgent = remaining is not None and remaining <= 3
        etd = QLabel(f"ETD {format_date_id(shipment['etd_belawan'])}")
        etd.setStyleSheet(theme.style(
            f"font-size: 11px; color: {'#dc2626' if urgent else '#6b7280'};"))
        layout.addWidget(etd)

        progress = QLabel(f"{done} / {total} langkah selesai")
        progress.setStyleSheet(theme.style("font-size: 11px; color: #6b7280;"))
        layout.addWidget(progress)


class Sidebar(Panel):
    new_shipment_clicked = pyqtSignal()
    shipment_selected = pyqtSignal(str)
    all_shipments_clicked = pyqtSignal()
    history_clicked = pyqtSignal()
    notifications_clicked = pyqtSignal()
    audit_clicked = pyqtSignal()
    monitor_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()
    home_clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setFixedWidth(250)
        self.setStyleSheet(theme.style("background: #f9fafb; border-right: 1px solid #e5e7eb;"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(10)

        title = QPushButton(APP_NAME)
        title.setFlat(True)
        title.setCursor(Qt.CursorShape.PointingHandCursor)
        title.setStyleSheet(theme.style(
            "border: none; font-size: 17px; font-weight: 700; color: #111827;"
            "text-align: left; padding: 0 4px;"))
        title.clicked.connect(self.home_clicked)
        layout.addWidget(title)

        new_button = PrimaryButton("⟳  Pindai Folder")
        new_button.clicked.connect(self.new_shipment_clicked)
        layout.addWidget(new_button)

        layout.addWidget(self._section_label("PENGIRIMAN AKTIF"))

        self.shipment_list = QListWidget()
        # Items are full-bleed: the entry widget paints the hover edge-to-edge,
        # so the item itself carries no radius or margin that would leave the
        # highlight looking chopped. Selection is still drawn by the item, and
        # shows through the entry's transparent background.
        self.shipment_list.setStyleSheet(theme.style("""
            QListWidget { background: transparent; border: none; }
            QListWidget::item { margin-bottom: 4px; }
            QListWidget::item:selected { background: #e0e7ff; border-radius: 6px; }
        """))
        self.shipment_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.shipment_list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.shipment_list, 1)

        self.see_all_button = _see_all_button()
        self.see_all_button.clicked.connect(self.all_shipments_clicked)
        layout.addWidget(self.see_all_button)

        self.empty_note = QLabel("Belum ada pengiriman aktif.")
        self.empty_note.setWordWrap(True)
        self.empty_note.setStyleSheet(theme.style("font-size: 11px; color: #9ca3af; padding: 4px;"))
        layout.addWidget(self.empty_note)

        layout.addWidget(self._divider())
        layout.addWidget(self._section_label("NAVIGASI"))

        self.history_button = self._nav_button("Riwayat")
        self.history_button.clicked.connect(self.history_clicked)
        layout.addWidget(self.history_button)

        self.notifications_button = self._nav_button("Notifikasi")
        self.notifications_button.clicked.connect(self.notifications_clicked)
        layout.addWidget(self.notifications_button)

        self.audit_button = self._nav_button("Log Aktivitas")
        self.audit_button.clicked.connect(self.audit_clicked)
        layout.addWidget(self.audit_button)

        self.monitor_button = self._nav_button("Monitor Kapal")
        self.monitor_button.clicked.connect(self.monitor_clicked)
        layout.addWidget(self.monitor_button)

        self.settings_button = self._nav_button("Pengaturan")
        self.settings_button.clicked.connect(self.settings_clicked)
        layout.addWidget(self.settings_button)

    # -- construction helpers -------------------------------------------

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(theme.style(
            "font-size: 10px; font-weight: 700; color: #9ca3af;"
            "letter-spacing: 1px; padding: 4px;"))
        return label

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(theme.style("color: #e5e7eb;"))
        return line

    def _nav_button(self, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setFlat(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(theme.style("""
            QPushButton { border: none; text-align: left; padding: 7px 8px;
                          border-radius: 6px; color: #374151; font-size: 13px; }
            QPushButton:hover { background: #eef2ff; }
        """))
        return button

    # -- state ------------------------------------------------------------

    def refresh(self, shipments, progress_lookup):
        self.shipment_list.clear()
        # The row must fit the sidebar width so the hover highlight fills it and
        # the wrapped vessel name is measured correctly. viewport().width() can
        # be unset before the first show, so fall back to the fixed geometry.
        width = self.shipment_list.viewport().width()
        if width < 50:
            width = 226   # 250px sidebar minus its layout margins
        for shipment in shipments:
            done, total = progress_lookup(shipment["id"])
            item = QListWidgetItem(self.shipment_list)
            item.setData(Qt.ItemDataRole.UserRole, shipment["id"])
            widget = ShipmentEntry(shipment, done, total)
            # Width 0 lets the item fill the viewport; the height comes from the
            # wrapped content at that width so nothing is clipped vertically.
            height = (widget.heightForWidth(width)
                      if widget.hasHeightForWidth() else widget.sizeHint().height())
            item.setSizeHint(QSize(0, height))
            self.shipment_list.addItem(item)
            self.shipment_list.setItemWidget(item, widget)

        has_any = bool(shipments)
        self.shipment_list.setVisible(has_any)
        self.empty_note.setVisible(not has_any)

    def select_shipment(self, shipment_id: str | None):
        if shipment_id is None:
            self.shipment_list.clearSelection()
            return
        for row in range(self.shipment_list.count()):
            item = self.shipment_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == shipment_id:
                self.shipment_list.setCurrentItem(item)
                return

    def set_notification_count(self, count: int):
        """Show the unread count as a badge on the Notifikasi nav item."""
        if count > 0:
            label = f"Notifikasi  ({count})" if count < 100 else "Notifikasi  (99+)"
            self.notifications_button.setText(label)
            self.notifications_button.setStyleSheet(theme.style("""
                QPushButton { border: none; text-align: left; padding: 7px 8px;
                              border-radius: 6px; color: #b91c1c; font-size: 13px;
                              font-weight: 700; }
                QPushButton:hover { background: #eef2ff; }
            """))
        else:
            self.notifications_button.setText("Notifikasi")
            self.notifications_button.setStyleSheet(theme.style("""
                QPushButton { border: none; text-align: left; padding: 7px 8px;
                              border-radius: 6px; color: #374151; font-size: 13px; }
                QPushButton:hover { background: #eef2ff; }
            """))

    def _on_item_clicked(self, item: QListWidgetItem):
        self.shipment_selected.emit(item.data(Qt.ItemDataRole.UserRole))

