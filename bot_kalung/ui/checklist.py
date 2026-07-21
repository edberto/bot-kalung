"""Workflow checklist (PRD Sections 6.2 and 6.3, revised 2026-07-21).

One flat, numbered list of steps — the fixed built-in steps plus any custom
TODO steps the worker adds. Each row carries a status icon, its number and
title, any action buttons, a manual checkbox, and an editable remark. Custom
steps can be moved or deleted and show who added them. The shipment is
completable once every step is ticked.
"""

from __future__ import annotations

from . import theme

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..core.constants import STEP_ACTIONS
from .widgets import DangerButton, Panel, SecondaryButton, format_date_id

ICON_DONE = "✓"
ICON_PENDING = "○"


def _who(email: str | None) -> str:
    """Short display name from a worker email (local part before @)."""
    if not email:
        return "?"
    return email.split("@", 1)[0]


def _when(iso: str | None) -> str:
    if not iso:
        return ""
    return format_date_id(iso[:10])


def _link_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(theme.style("""
        QPushButton { border: none; background: transparent; color: #2563eb;
                      font-size: 11px; padding: 2px 4px; text-align: left; }
        QPushButton:hover { color: #1d4ed8; text-decoration: underline; }
    """))
    return button


class StepRow(Panel):
    """One workflow step (built-in or custom)."""

    toggled = pyqtSignal(str, bool)      # step code, complete
    action = pyqtSignal(str, str, str)   # step code, kind, payload
    remark_edited = pyqtSignal(str, str)  # step code, text
    delete_requested = pyqtSignal(str)   # custom step code
    move_requested = pyqtSignal(str, str)  # step code, "up" | "down"

    def __init__(self, state):
        super().__init__()
        self.code = state.code
        self.is_custom = state.is_custom
        self.phase2_only = state.phase2_only
        self.complete = state.is_complete
        self.overdue = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self.icon = QLabel(ICON_DONE if self.complete else ICON_PENDING)
        self.icon.setFixedWidth(16)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setSpacing(2)

        self.title = QLabel(f"{state.display_number}. {state.title}")
        self.title.setWordWrap(True)
        self.title.setStyleSheet(theme.style(
            "border: none; font-size: 13px; font-weight: 600; color: #111827;"))
        text_column.addWidget(self.title)

        if state.description:
            desc = QLabel(state.description)
            desc.setWordWrap(True)
            desc.setTextFormat(Qt.TextFormat.PlainText)
            desc.setStyleSheet(theme.style(
                "border: none; font-size: 11px; color: #6b7280;"))
            text_column.addWidget(desc)

        if state.is_custom:
            attribution = QLabel(
                f"ditambahkan oleh {_who(state.added_by)} · {_when(state.added_at)}")
            attribution.setStyleSheet(theme.style(
                "border: none; font-size: 10px; color: #9ca3af;"))
            text_column.addWidget(attribution)

        # -- remark ----------------------------------------------------------
        self.remark_label = QLabel()
        self.remark_label.setWordWrap(True)
        self.remark_label.setTextFormat(Qt.TextFormat.PlainText)
        self.remark_label.setStyleSheet(theme.style(
            "border: none; font-size: 11px; color: #b45309;"))
        text_column.addWidget(self.remark_label)

        self.remark_edit = QLineEdit()
        self.remark_edit.setPlaceholderText("Tulis catatan…")
        self.remark_edit.setStyleSheet(theme.style(
            "border: 1px solid #d1d5db; border-radius: 4px; padding: 3px 6px;"
            "font-size: 11px;"))
        self.remark_edit.editingFinished.connect(self._commit_remark)
        self.remark_edit.hide()
        text_column.addWidget(self.remark_edit)

        controls = QHBoxLayout()
        controls.setSpacing(4)
        self.remark_button = _link_button("Catatan")
        self.remark_button.clicked.connect(self._begin_remark)
        controls.addWidget(self.remark_button)
        if state.is_custom:
            up = _link_button("▲")
            up.setToolTip("Pindah ke atas")
            up.clicked.connect(lambda: self.move_requested.emit(self.code, "up"))
            controls.addWidget(up)
            down = _link_button("▼")
            down.setToolTip("Pindah ke bawah")
            down.clicked.connect(lambda: self.move_requested.emit(self.code, "down"))
            controls.addWidget(down)
        controls.addStretch(1)
        text_column.addLayout(controls)

        layout.addLayout(text_column, 1)

        # -- action buttons (built-in only) ----------------------------------
        self.buttons: list[QPushButton] = []
        for label, kind, payload in STEP_ACTIONS.get(state.code, []):
            button = SecondaryButton(label)
            button.clicked.connect(
                lambda _, k=kind, p=payload: self.action.emit(self.code, k, p))
            self.buttons.append(button)
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)

        if state.is_custom:
            delete = DangerButton("Hapus")
            delete.setMinimumHeight(28)
            delete.clicked.connect(
                lambda: self.delete_requested.emit(self.code))
            layout.addWidget(delete, 0, Qt.AlignmentFlag.AlignVCenter)

        self.checkbox = QCheckBox()
        self.checkbox.setToolTip("Tandai selesai / belum selesai")
        self.checkbox.setChecked(self.complete)
        self.checkbox.clicked.connect(
            lambda checked: self.toggled.emit(self.code, checked))
        layout.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignVCenter)

        self._show_remark(state.remark, state.remark_author)
        self._restyle()

    # -- remark editing ----------------------------------------------------

    def _show_remark(self, remark: str | None, author: str | None):
        if remark:
            who = f"  — {_who(author)}" if author else ""
            self.remark_label.setText(f"Catatan: {remark}{who}")
            self.remark_label.show()
            self.remark_button.setText("Ubah catatan")
        else:
            self.remark_label.hide()
            self.remark_button.setText("Catatan")
        self._current_remark = remark or ""

    def _begin_remark(self):
        self.remark_edit.setText(self._current_remark)
        self.remark_edit.show()
        self.remark_edit.setFocus()

    def _commit_remark(self):
        if not self.remark_edit.isVisible():
            return
        text = self.remark_edit.text().strip()
        self.remark_edit.hide()
        if text != self._current_remark:
            self.remark_edited.emit(self.code, text)

    # -- appearance --------------------------------------------------------

    def set_overdue(self, overdue: bool):
        self.overdue = overdue and not self.complete
        self._restyle()

    def _restyle(self):
        if self.complete:
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
            f"color: {'#6b7280' if self.complete else '#111827'};"))


class ChecklistView(QWidget):
    """The full flat checklist for one shipment, rebuilt per shipment."""

    step_toggled = pyqtSignal(str, bool)
    action_requested = pyqtSignal(str, str, str)
    mark_complete_requested = pyqtSignal()
    remark_edited = pyqtSignal(str, str)       # code, text
    add_requested = pyqtSignal()               # detail view runs the dialog
    delete_requested = pyqtSignal(str)         # custom code
    move_requested = pyqtSignal(str, str)      # code, "up" | "down"

    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(6)

        self.rows: dict[str, StepRow] = {}

        self.list_layout = QVBoxLayout()
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.layout.addLayout(self.list_layout)

        self.add_button = SecondaryButton("+  Tambah Langkah")
        self.add_button.clicked.connect(self.add_requested)
        self.layout.addWidget(self.add_button)

        # PRD 6.4 — appears once every step (built-in + custom) is done.
        self.complete_banner = Panel()
        self.complete_banner.setStyleSheet(theme.style(
            f"background: {theme.banner('success')[1]};"
            f"border: 1px solid {theme.banner('success')[2]}; border-radius: 8px;"))
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
        self.layout.addWidget(self.complete_banner)

        self.layout.addStretch(1)

    def apply(self, steps, *, overdue: bool):
        """Rebuild every row from the ordered step states."""
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.rows.clear()

        for state in steps:
            row = StepRow(state)
            row.set_overdue(overdue)
            row.toggled.connect(self.step_toggled)
            row.action.connect(self.action_requested)
            row.remark_edited.connect(self.remark_edited)
            row.delete_requested.connect(self.delete_requested)
            row.move_requested.connect(self.move_requested)
            self.rows[state.code] = row
            self.list_layout.addWidget(row)

        countable = [s for s in steps if s.countable]
        all_done = bool(countable) and all(s.is_complete for s in countable)
        self.complete_banner.setVisible(all_done)
