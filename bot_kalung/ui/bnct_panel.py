"""Per-shipment BNCT monitoring panel on the Shipment Detail view (PRD 15).

Reads the latest stored check and renders it in Bahasa Indonesia. Purely a
view of what the controller has already recorded — it does no polling itself,
so opening a shipment never blocks on the network.
"""

from __future__ import annotations

from . import theme

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from ..services.bnct_monitor import BnctMonitor
from .bnct_display import describe_record
from .widgets import Panel, SecondaryButton, format_date_id


def _checked_at(iso: str) -> str:
    if not iso or "T" not in iso:
        return format_date_id(iso)
    date, time = iso.split("T", 1)
    return f"{format_date_id(date)} {time[:5]}"


class BnctPanel(Panel):
    """Latest BNCT status for one shipment, with a manual refresh button."""

    def __init__(self, db, on_refresh=None):
        super().__init__()
        self.monitor = BnctMonitor(db)
        self._on_refresh = on_refresh
        self.shipment_id: str | None = None

        self.setStyleSheet(theme.style(
            "background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 8px;"))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(6)

        top = QHBoxLayout()
        title = QLabel("Pemantauan BNCT")
        title.setStyleSheet(theme.style(
            "font-size: 13px; font-weight: 700; color: #111827; border: none;"))
        top.addWidget(title)
        top.addStretch(1)

        self.refresh_button = SecondaryButton("Periksa Sekarang")
        self.refresh_button.setMinimumHeight(28)
        self.refresh_button.clicked.connect(self._refresh_clicked)
        top.addWidget(self.refresh_button)
        outer.addLayout(top)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setStyleSheet(theme.style(
            "font-size: 13px; color: #374151; border: none;"))
        outer.addWidget(self.status_label)

        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.setStyleSheet(theme.style(
            "font-size: 12px; color: #6b7280; border: none;"))
        outer.addWidget(self.detail_label)

        self.time_label = QLabel()
        self.time_label.setStyleSheet(theme.style(
            "font-size: 11px; color: #9ca3af; border: none;"))
        outer.addWidget(self.time_label)

    def _refresh_clicked(self):
        if self._on_refresh is not None:
            self._on_refresh()

    def load(self, shipment_id: str | None):
        self.shipment_id = shipment_id
        if shipment_id is None:
            return
        check = self.monitor.latest(shipment_id)

        if check is None:
            self.status_label.setText("Belum ada pemeriksaan.")
            self.status_label.setStyleSheet(theme.style(
                "font-size: 13px; color: #6b7280; border: none;"))
            self.detail_label.setText("")
            self.time_label.setText("")
            return

        color, status, detail = describe_record(check)
        self.status_label.setText(status)
        self.status_label.setStyleSheet(theme.style(
            f"font-size: 13px; font-weight: 600; color: {color}; border: none;"))
        self.detail_label.setText(detail)
        self.time_label.setText(f"Diperiksa: {_checked_at(check['checked_at'])}")
