"""Workflow checklist (PRD Sections 6.2 and 6.3).

Five collapsible phases; each step row carries a status icon, its title and
description, any action buttons, and a manual checkbox that always works.
"""

from __future__ import annotations

from . import theme

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..core.constants import (
    STEP_ACTIONS, WORKFLOW_PHASES, WORKFLOW_STEPS,
)
from .widgets import Panel, SecondaryButton

ICON_DONE = "✓"
ICON_PENDING = "○"
ICON_PHASE2 = "—"


class StepRow(Panel):
    """One workflow step."""

    toggled = pyqtSignal(str, bool)      # step code, complete
    action = pyqtSignal(str, str, str)   # step code, kind, payload

    def __init__(self, code: str, title: str, description: str,
                 phase2_only: bool):
        super().__init__()
        self.code = code
        self.phase2_only = phase2_only
        self.complete = False
        self.overdue = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self.icon = QLabel(ICON_PHASE2 if phase2_only else ICON_PENDING)
        self.icon.setFixedWidth(16)
        self.icon.setStyleSheet(theme.style("border: none; color: #9ca3af; font-size: 14px;"))
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self.title = QLabel(f"{code}  {title}")
        self.title.setStyleSheet(theme.style(
            "border: none; font-size: 13px; font-weight: 600; color: #111827;"))
        title_row.addWidget(self.title)
        if phase2_only:
            badge = QLabel("Segera hadir")
            badge.setStyleSheet(theme.style(
                "background: #f3f4f6; color: #6b7280; border-radius: 3px;"
                "padding: 1px 6px; font-size: 10px;"))
            title_row.addWidget(badge)
        title_row.addStretch(1)
        text_column.addLayout(title_row)

        self.description = QLabel(description)
        self.description.setWordWrap(True)
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        self.description.setStyleSheet(theme.style(
            "border: none; font-size: 11px; color: #6b7280;"))
        text_column.addWidget(self.description)
        layout.addLayout(text_column, 1)

        self.buttons: list[QPushButton] = []
        for label, kind, payload in STEP_ACTIONS.get(code, []):
            button = SecondaryButton(label)
            button.setEnabled(not phase2_only)
            button.clicked.connect(
                lambda _, k=kind, p=payload: self.action.emit(self.code, k, p))
            self.buttons.append(button)
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.checkbox = QCheckBox()
        self.checkbox.setEnabled(not phase2_only)
        self.checkbox.setToolTip("Tandai selesai / belum selesai")
        self.checkbox.clicked.connect(
            lambda checked: self.toggled.emit(self.code, checked))
        layout.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignVCenter)

        self._restyle()

    def set_state(self, complete: bool, overdue: bool):
        self.complete = complete
        self.overdue = overdue
        # Avoid re-emitting while syncing from the database.
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(complete)
        self.checkbox.blockSignals(False)
        self.icon.setText(
            ICON_PHASE2 if self.phase2_only
            else (ICON_DONE if complete else ICON_PENDING))
        self._restyle()

    def _restyle(self):
        if self.phase2_only:
            border, background = "#e5e7eb", "#fafafa"
        elif self.complete:
            border, background = "#16a34a", "#f6fef9"
        elif self.overdue:
            border, background = "#ea580c", "#fffbf5"
        else:
            border, background = "#e5e7eb", "white"

        self.setStyleSheet(theme.style(
            f"background: {background}; border: 1px solid #e5e7eb;"
            f"border-left: 3px solid {border}; border-radius: 6px;"))
        self.icon.setStyleSheet(theme.style(
            "border: none; font-size: 14px;"
            f"color: {'#16a34a' if self.complete else '#9ca3af'};"))
        self.title.setStyleSheet(theme.style(
            "border: none; font-size: 13px; font-weight: 600;"
            f"color: {'#6b7280' if self.complete or self.phase2_only else '#111827'};"))


class PhaseSection(QWidget):
    """A collapsible group of steps."""

    def __init__(self, key: str, title: str):
        super().__init__()
        self.key = key
        self.expanded = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.header = QPushButton()
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setStyleSheet(theme.style("""
            QPushButton { border: none; text-align: left; padding: 6px 2px;
                          font-size: 12px; font-weight: 700; color: #374151; }
            QPushButton:hover { color: #111827; }
        """))
        self.header.clicked.connect(self.toggle)
        layout.addWidget(self.header)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(6)
        layout.addWidget(self.body)

        self.title_text = title
        self.rows: list[StepRow] = []
        self._update_header()

    def add_row(self, row: StepRow):
        self.rows.append(row)
        self.body_layout.addWidget(row)

    def toggle(self):
        self.expanded = not self.expanded
        self.body.setVisible(self.expanded)
        self._update_header()

    def _update_header(self):
        done = sum(1 for r in self.rows if r.complete and not r.phase2_only)
        total = sum(1 for r in self.rows if not r.phase2_only)
        arrow = "▾" if self.expanded else "▸"
        self.header.setText(f"{arrow}  {self.title_text}   ({done}/{total})")

    def refresh_header(self):
        self._update_header()


class ChecklistView(QWidget):
    """The full 5-phase checklist for one shipment."""

    step_toggled = pyqtSignal(str, bool)
    action_requested = pyqtSignal(str, str, str)
    mark_complete_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.rows: dict[str, StepRow] = {}
        self.sections: list[PhaseSection] = []

        for key, title in WORKFLOW_PHASES:
            section = PhaseSection(key, title)
            for code, phase, step_title, description, _auto, phase2 in WORKFLOW_STEPS:
                if phase != key:
                    continue
                row = StepRow(code, step_title, description, phase2)
                row.toggled.connect(self.step_toggled)
                row.action.connect(self.action_requested)
                self.rows[code] = row
                section.add_row(row)
            section.refresh_header()
            self.sections.append(section)
            layout.addWidget(section)

        # PRD 6.4 — appears once every Phase E step is done.
        self.complete_banner = Panel()
        self.complete_banner.setStyleSheet(theme.style(
            f"background: {theme.banner('success')[1]};"
            f"border: 1px solid {theme.banner('success')[2]};"
            "border-radius: 8px;"))
        banner_layout = QHBoxLayout(self.complete_banner)
        banner_layout.setContentsMargins(14, 10, 14, 10)
        label = QLabel("Semua langkah selesai.")
        label.setStyleSheet(
            f"border: none; color: {theme.banner('success')[0]}; font-size: 13px;")
        banner_layout.addWidget(label)
        banner_layout.addStretch(1)
        self.complete_button = SecondaryButton("Tandai Pengiriman Selesai")
        self.complete_button.clicked.connect(self.mark_complete_requested)
        banner_layout.addWidget(self.complete_button)
        self.complete_banner.setVisible(False)
        layout.addWidget(self.complete_banner)

        layout.addStretch(1)

    def apply(self, steps, *, overdue: bool):
        """Sync every row from the stored step states."""
        by_code = {s.code: s for s in steps}
        for code, row in self.rows.items():
            state = by_code.get(code)
            complete = bool(state and state.is_complete)
            row.set_state(complete,
                          overdue=overdue and not complete and not row.phase2_only)
        for section in self.sections:
            section.refresh_header()

        phase_e = [s for s in WORKFLOW_STEPS if s[1] == "E" and not s[5]]
        all_done = all(by_code.get(code) and by_code[code].is_complete
                       for code, *_ in phase_e)
        self.complete_banner.setVisible(bool(phase_e) and all_done)
