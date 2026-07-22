"""The shipment card (PRD 4.1).

Lived on the dashboard until 2026-07-22, when the dashboard became the month
calendar and the cards moved to the "Semua Pengiriman" screen.
"""

from __future__ import annotations

from . import theme

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QVBoxLayout,
)

from ..core.constants import EXPORTER_COLORS
from .widgets import Panel, SecondaryButton, days_until, format_date_id


class ShipmentCard(Panel):
    open_requested = pyqtSignal(str)

    def __init__(self, shipment, done: int, total: int):
        super().__init__()
        self.shipment_id = shipment["id"]
        # The whole card is clickable — the "Buka" button is a secondary
        # affordance. Object-name scoped so the hover styles the card, not its
        # child labels.
        self.setObjectName("shipmentCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(theme.style(
            "#shipmentCard { background: white; border: 1px solid #e5e7eb;"
            " border-radius: 8px; }"
            "#shipmentCard:hover { background: #f8fafc; border: 1px solid #93c5fd; }"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        header = QHBoxLayout()
        code = shipment["exporter_code"]
        badge = QLabel(f"{code}{shipment['sequence_number']}")
        badge.setStyleSheet(theme.style(
            f"background: {EXPORTER_COLORS.get(code, '#6b7280')}; color: white;"
            "border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;"))
        header.addWidget(badge)
        header.addStretch(1)

        # PRD 4.1 — ETD shown in red when three days out or less.
        remaining = days_until(shipment["etd_belawan"])
        urgent = remaining is not None and remaining <= 3
        etd = QLabel(f"ETD {format_date_id(shipment['etd_belawan'])}")
        etd.setStyleSheet(theme.style(
            f"font-size: 12px; font-weight: 600; border: none;"
            f"color: {'#dc2626' if urgent else '#374151'};"))
        header.addWidget(etd)
        layout.addLayout(header)

        vessel = f"{shipment['vessel_name'] or ''} {shipment['voyage'] or ''}".strip()
        vessel_label = QLabel(vessel or "(tanpa kapal)")
        vessel_label.setStyleSheet(theme.style(
            "font-size: 15px; font-weight: 600; color: #111827; border: none;"))
        layout.addWidget(vessel_label)

        detail = QLabel(f"{shipment['destination_port'] or '-'}  ·  "
                        f"Booking {shipment['booking_number'] or '-'}")
        detail.setStyleSheet(theme.style("font-size: 12px; color: #6b7280; border: none;"))
        layout.addWidget(detail)

        bar = QProgressBar()
        bar.setRange(0, max(total, 1))
        bar.setValue(done)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setStyleSheet(theme.style("""
            QProgressBar { background: #f3f4f6; border: none; border-radius: 3px; }
            QProgressBar::chunk { background: #2563eb; border-radius: 3px; }
        """))
        layout.addWidget(bar)

        footer = QHBoxLayout()
        count = QLabel(f"{done} dari {total} langkah selesai")
        count.setStyleSheet(theme.style("font-size: 11px; color: #6b7280; border: none;"))
        footer.addWidget(count)
        footer.addStretch(1)
        open_button = SecondaryButton("Buka")
        open_button.clicked.connect(lambda: self.open_requested.emit(self.shipment_id))
        footer.addWidget(open_button)
        layout.addLayout(footer)

    def mousePressEvent(self, event):
        # Clicking anywhere on the card opens it; the "Buka" button consumes
        # its own clicks, so this never double-fires.
        self.open_requested.emit(self.shipment_id)
