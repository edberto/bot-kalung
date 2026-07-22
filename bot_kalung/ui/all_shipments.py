"""All shipments — active and completed (2026-07-22).

The dashboard is the month calendar now, so the shipment cards live here,
reached from the sidebar's "Lihat Semua". Active shipments come first, then
completed ones, each under its own heading.
"""

from __future__ import annotations

from . import theme

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from ..services.shipments import Shipments
from .shipment_card import ShipmentCard


class AllShipmentsView(QWidget):
    shipment_opened = pyqtSignal(str)

    def __init__(self, db):
        super().__init__()
        self.shipments = Shipments(db)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(12)

        title = QLabel("Semua Pengiriman")
        title.setStyleSheet(theme.style(
            "font-size: 22px; font-weight: 700; color: #111827;"))
        outer.addWidget(title)

        self.count_label = QLabel()
        self.count_label.setStyleSheet(theme.style(
            "font-size: 12px; color: #6b7280;"))
        outer.addWidget(self.count_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.host = QWidget()
        self.body = QVBoxLayout(self.host)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(14)
        self.scroll.setWidget(self.host)
        outer.addWidget(self.scroll, 1)

        self.empty_state = QLabel("Belum ada pengiriman.")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setStyleSheet(theme.style(
            "font-size: 14px; color: #9ca3af;"))
        outer.addWidget(self.empty_state)

    def refresh(self):
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                self._drop(item.layout())

        active = self.shipments.active()
        completed = self.shipments.completed()
        self.count_label.setText(
            f"{len(active)} aktif · {len(completed)} selesai")

        self.cards: dict[str, ShipmentCard] = {}
        for heading, rows in (("Aktif", active), ("Selesai", completed)):
            if not rows:
                continue
            label = QLabel(heading.upper())
            label.setStyleSheet(theme.style(
                "font-size: 10px; font-weight: 700; color: #9ca3af;"
                "letter-spacing: 1px;"))
            self.body.addWidget(label)

            grid = QGridLayout()
            grid.setSpacing(12)
            for index, row in enumerate(rows):
                done, total = self.shipments.progress(row["id"])
                card = ShipmentCard(row, done, total)
                card.open_requested.connect(self.shipment_opened)
                self.cards[row["id"]] = card
                grid.addWidget(card, index // 2, index % 2)
            self.body.addLayout(grid)

        self.body.addStretch(1)
        has_any = bool(active or completed)
        self.scroll.setVisible(has_any)
        self.empty_state.setVisible(not has_any)

    def _drop(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                self._drop(item.layout())
