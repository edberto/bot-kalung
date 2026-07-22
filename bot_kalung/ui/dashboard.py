"""Dashboard view — the month calendar (PRD Section 4, revised 2026-07-22).

The dashboard used to carry stat cards and a grid of shipment cards. Both were
redundant with the sidebar (which lists the active shipments) and left the
calendar too little room, so the dashboard is now the calendar alone; the cards
moved to the "Semua Pengiriman" screen behind the sidebar's "Lihat Semua".
"""

from __future__ import annotations

from . import theme

from datetime import date

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .calendar_view import MonthCalendar
from .widgets import MONTHS_ID, SecondaryButton


class DashboardView(QWidget):
    new_shipment_clicked = pyqtSignal()
    shipment_opened = pyqtSignal(str)
    resequence_requested = pyqtSignal()
    calendar_entry_clicked = pyqtSignal(str, str)    # shipment id, step code
    calendar_range_requested = pyqtSignal(str, str)  # start iso, end iso

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setStyleSheet(theme.style(
            "font-size: 22px; font-weight: 700; color: #111827;"))
        header.addWidget(title)
        header.addStretch(1)
        self.resequence_button = SecondaryButton("Ubah Nomor Urut")
        self.resequence_button.clicked.connect(self.resequence_requested)
        header.addWidget(self.resequence_button)
        self.today_label = QLabel()
        self.today_label.setStyleSheet(theme.style(
            "font-size: 12px; color: #6b7280;"))
        header.addWidget(self.today_label)
        outer.addLayout(header)

        self.calendar = MonthCalendar()
        self.calendar.entry_clicked.connect(self.calendar_entry_clicked)
        self.calendar.month_changed.connect(self.calendar_range_requested)
        outer.addWidget(self.calendar, 1)

    def refresh(self, shipments, progress_lookup, *, overdue: int,
                this_month: int):
        """Signature kept for the main window; only the date and the calendar
        need refreshing now that the cards live elsewhere.
        """
        today = date.today()
        self.today_label.setText(
            f"{today.day} {MONTHS_ID[today.month - 1]} {today.year}")
        self.calendar.reload()
