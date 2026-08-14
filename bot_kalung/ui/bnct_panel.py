"""Compact per-shipment vessel status on the Shipment Detail view.

Shows only the coarse BNCT state (belum terjadwal / terjadwal / sandar /
berangkat) and a button to the full Monitor Kapal board, where the schedule and
loading detail live. Purely a view of the latest stored check — it does no
polling, so opening a shipment never blocks on the network.
"""

from __future__ import annotations

from . import theme

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from ..services.bnct_monitor import BnctMonitor
from .bnct_display import describe_record
from .widgets import CARD_STYLE, Panel, SecondaryButton, format_date_id, section_header


def _checked_at(iso: str) -> str:
    if not iso or "T" not in iso:
        return format_date_id(iso)
    date, time = iso.split("T", 1)
    return f"{format_date_id(date)} {time[:5]}"


class BnctPanel(Panel):
    """Latest coarse BNCT state for one shipment + a link to Monitor Kapal."""

    open_monitor_requested = pyqtSignal()

    def __init__(self, db):
        super().__init__()
        self.monitor = BnctMonitor(db)
        self.shipment_id: str | None = None

        self.setStyleSheet(theme.style(CARD_STYLE))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(section_header("Kapal (BNCT)"))
        top.addStretch(1)
        self.monitor_button = SecondaryButton("Buka Monitor Kapal")
        self.monitor_button.setMinimumHeight(28)
        self.monitor_button.clicked.connect(self.open_monitor_requested)
        top.addWidget(self.monitor_button)
        outer.addLayout(top)

        # A solid pill in the state's colour (matches the Monitor Kapal board).
        self.status_chip = QLabel()
        self.status_chip.setStyleSheet(theme.style("border: none;"))
        chip_row = QHBoxLayout()
        chip_row.addWidget(self.status_chip)
        chip_row.addStretch(1)
        outer.addLayout(chip_row)

        self.time_label = QLabel()
        self.time_label.setStyleSheet(theme.style(
            "border: none; background: transparent; font-size: 11px; color: #9ca3af;"))
        outer.addWidget(self.time_label)
        outer.addStretch(1)

    def _set_chip(self, text: str, color: str):
        self.status_chip.setText(text)
        self.status_chip.setStyleSheet(theme.style(
            f"border: none; background: {color}; color: #ffffff;"
            "border-radius: 6px; padding: 5px 14px; font-size: 13px;"
            "font-weight: 700;"))

    def load(self, shipment_id: str | None):
        self.shipment_id = shipment_id
        if shipment_id is None:
            return
        check = self.monitor.latest(shipment_id)
        if check is None:
            self._set_chip("Belum diperiksa", "#6b7280")
            self.time_label.setText("")
            return
        color, status, _ = describe_record(check)
        self._set_chip(status, color)
        self.time_label.setText(f"Diperiksa: {_checked_at(check['checked_at'])}")
