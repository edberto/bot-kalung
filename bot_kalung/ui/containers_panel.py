"""Container list for a shipment (folder-scan tracker, Phase 4).

Shows each container read from the VGM with its latest BNCT status; a "Buka di
BNCT" button copies the container number and opens the portal (the portal has no
URL pre-fill, so the worker pastes the number into the container search).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import theme
from .widgets import Panel, SecondaryButton


class ContainersPanel(QWidget):
    open_bnct = pyqtSignal(str)      # container number

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.heading = QLabel("Kontainer")
        self.heading.setStyleSheet(theme.style(
            "font-size: 14px; font-weight: 700; color: #111827;"))
        layout.addWidget(self.heading)

        self.list_layout = QVBoxLayout()
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        layout.addLayout(self.list_layout)

    def apply(self, containers):
        while self.list_layout.count():
            taken = self.list_layout.takeAt(0)
            if taken.widget() is not None:
                taken.widget().deleteLater()

        self.heading.setText(f"Kontainer ({len(containers)})")
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
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = container.container_no
        detail = " ".join(filter(None, [container.size, container.type]))
        if detail:
            title += f"  ·  {detail}"
        name = QLabel(title)
        name.setStyleSheet(theme.style(
            "border: none; font-size: 13px; font-weight: 600; color: #111827;"))
        text.addWidget(name)

        status = QLabel(container.status)
        status.setStyleSheet(theme.style(
            "border: none; font-size: 11px; font-weight: 600;"
            f"color: {'#16a34a' if stack else '#6b7280'};"))
        text.addWidget(status)
        row.addLayout(text, 1)

        button = SecondaryButton("Buka di BNCT")
        button.setMinimumHeight(28)
        button.clicked.connect(
            lambda: self.open_bnct.emit(container.container_no))
        row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        return card
