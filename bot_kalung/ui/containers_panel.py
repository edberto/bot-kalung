"""Container list for a shipment (folder-scan tracker).

Shows each container read from the VGM with its latest BNCT status and which
terminal to look at. One shared "Buka di BNCT" button opens the portal and copies
the container numbers (the portal has no URL pre-fill, so the worker pastes each
number into the container search).
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import theme
from .widgets import Panel, SecondaryButton

# The BNCT container search's terminal codes.
_TERMINAL = {"PTP": "PTP", "TPKB": "TPKB"}


class ContainersPanel(QWidget):
    open_bnct = pyqtSignal()      # open the portal + copy the numbers

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.heading = QLabel("Kontainer")
        self.heading.setStyleSheet(theme.style(
            "font-size: 14px; font-weight: 700; color: #111827;"))
        top.addWidget(self.heading)
        top.addStretch(1)
        self.bnct_button = SecondaryButton("Buka di BNCT")
        self.bnct_button.setMinimumHeight(28)
        self.bnct_button.clicked.connect(self.open_bnct)
        top.addWidget(self.bnct_button)
        layout.addLayout(top)

        self.list_layout = QVBoxLayout()
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        layout.addLayout(self.list_layout)
        layout.addStretch(1)

    def apply(self, containers):
        while self.list_layout.count():
            taken = self.list_layout.takeAt(0)
            if taken.widget() is not None:
                taken.widget().deleteLater()

        self.heading.setText(f"Kontainer ({len(containers)})")
        self.bnct_button.setEnabled(bool(containers))
        if not containers:
            empty = QLabel("Belum ada kontainer terbaca dari VGM.")
            empty.setStyleSheet(theme.style("font-size: 12px; color: #9ca3af;"))
            self.list_layout.addWidget(empty)
            return

        for container in containers:
            self.list_layout.addWidget(self._row(container))

    def _row(self, container) -> QWidget:
        stack = container.at_stack_receiving
        accent = "#16a34a" if stack else "#e5e7eb"
        card = Panel()
        card.setStyleSheet(theme.style(
            "background: white; border: 1px solid #e5e7eb;"
            f"border-left: 3px solid {accent}; border-radius: 6px;"))
        row = QVBoxLayout(card)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(2)

        title = container.container_no
        detail = " ".join(filter(None, [container.size, container.type]))
        if detail:
            title += f"  ·  {detail}"
        name = QLabel(title)
        name.setStyleSheet(theme.style(
            "border: none; font-size: 13px; font-weight: 600; color: #111827;"))
        row.addWidget(name)

        status = QLabel(container.status)
        status.setStyleSheet(theme.style(
            "border: none; font-size: 11px; font-weight: 600;"
            f"color: {'#16a34a' if stack else '#6b7280'};"))
        row.addWidget(status)

        # Which terminal to search on the portal.
        site = container.last_site
        where = (f"Cari di terminal {_TERMINAL.get(site, site)}" if site
                 else "Cari di terminal PTP & TPKB")
        terminal = QLabel(where)
        terminal.setStyleSheet(theme.style(
            "border: none; font-size: 11px; color: #6b7280;"))
        row.addWidget(terminal)
        return card
