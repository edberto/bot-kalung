"""Dashboard view (PRD Section 4)."""

from __future__ import annotations

from . import theme

from datetime import date

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QVBoxLayout,
    QWidget,
)

from ..core.constants import EXPORTER_COLORS
from .widgets import (
    MONTHS_ID, Panel, PrimaryButton, SecondaryButton, days_until, format_date_id,
)


class StatCard(Panel):
    def __init__(self, caption: str, value: str, accent: str = "#111827"):
        super().__init__()
        self.setStyleSheet(theme.style(
            "background: white; border: 1px solid #e5e7eb; border-radius: 8px;"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(2)

        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(theme.style(
            f"font-size: 26px; font-weight: 700; color: {accent}; border: none;"))
        caption_label = QLabel(caption)
        caption_label.setStyleSheet(theme.style("font-size: 12px; color: #6b7280; border: none;"))
        layout.addWidget(self.value_label)
        layout.addWidget(caption_label)


class ShipmentCard(Panel):
    open_requested = pyqtSignal(str)

    def __init__(self, shipment, done: int, total: int):
        super().__init__()
        self.shipment_id = shipment["id"]
        self.setStyleSheet(theme.style(
            "background: white; border: 1px solid #e5e7eb; border-radius: 8px;"))

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


class DashboardView(QWidget):
    new_shipment_clicked = pyqtSignal()
    shipment_opened = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(18)

        header = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setStyleSheet(theme.style("font-size: 22px; font-weight: 700; color: #111827;"))
        header.addWidget(title)
        header.addStretch(1)
        self.today_label = QLabel()
        self.today_label.setStyleSheet(theme.style("font-size: 12px; color: #6b7280;"))
        header.addWidget(self.today_label)
        outer.addLayout(header)

        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(12)
        outer.addLayout(self.stats_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.scroll.setWidget(self.grid_host)
        outer.addWidget(self.scroll, 1)

        self.empty_state = QLabel(
            "Belum ada pengiriman aktif.\nMulai dengan menekan + Pengiriman Baru.")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setStyleSheet(theme.style("font-size: 14px; color: #9ca3af;"))
        outer.addWidget(self.empty_state)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        new_button = PrimaryButton("+  Pengiriman Baru")
        new_button.clicked.connect(self.new_shipment_clicked)
        bottom.addWidget(new_button)
        bottom.addStretch(1)
        outer.addLayout(bottom)

    def refresh(self, shipments, progress_lookup, *, overdue: int, this_month: int):
        today = date.today()
        self.today_label.setText(
            f"{today.day} {MONTHS_ID[today.month - 1]} {today.year}")

        while self.stats_row.count():
            item = self.stats_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.stats_row.addWidget(StatCard("Pengiriman aktif", str(len(shipments))))
        self.stats_row.addWidget(StatCard(
            "Langkah terlambat", str(overdue),
            accent="#dc2626" if overdue else "#111827"))
        self.stats_row.addWidget(StatCard("Selesai bulan ini", str(this_month)))

        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for index, shipment in enumerate(shipments):
            done, total = progress_lookup(shipment["id"])
            card = ShipmentCard(shipment, done, total)
            card.open_requested.connect(self.shipment_opened)
            self.grid.addWidget(card, index // 2, index % 2)
        if shipments:
            self.grid.setRowStretch(len(shipments) // 2 + 1, 1)

        has_any = bool(shipments)
        self.scroll.setVisible(has_any)
        self.empty_state.setVisible(not has_any)

