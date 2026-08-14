"""Action items for a scanned shipment.

Replaces the boolean workflow checklist: one row per action item (document or
task), each with a status dropdown instead of a done/pending checkbox, an
optional due date, and a delete button. A free-text notes box for the whole
shipment sits at the bottom. Ad-hoc items are added with the button.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QTextEdit, QVBoxLayout, QWidget,
)

from . import theme
from ..core.constants import ACTION_STATUS_LABELS, ACTION_STATUSES
from .widgets import ComboBox, DangerButton, Panel, SecondaryButton, format_date_id

# Colour per status, from "not started" grey to "final" green.
_STATUS_COLOR = {
    "pending": "#9ca3af",
    "in_progress": "#2563eb",
    "draft_received": "#7c3aed",
    "draft_revision": "#d97706",
    "draft_ok": "#0891b2",
    "final": "#16a34a",
}


def _link_button(text: str) -> SecondaryButton:
    button = SecondaryButton(text)
    button.setMinimumHeight(28)
    return button


class ActionItemRow(Panel):
    """One action item: a status dot, its title + optional date, a status
    dropdown, a date button, and a delete button.
    """

    status_changed = pyqtSignal(str, str)     # item id, status
    delete_requested = pyqtSignal(str)        # item id
    date_edit_requested = pyqtSignal(str)     # item id

    def __init__(self, item):
        super().__init__()
        self.item_id = item.id
        color = _STATUS_COLOR.get(item.status, "#9ca3af")
        self.setStyleSheet(theme.style(
            f"background: white; border: 1px solid #e5e7eb;"
            f"border-left: 3px solid {color}; border-radius: 6px;"))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        title = QLabel(item.title + ("  (tambahan)" if item.is_custom else ""))
        title.setWordWrap(True)
        title.setStyleSheet(theme.style(
            "border: none; font-size: 13px; font-weight: 600; color: #111827;"))
        text_column.addWidget(title)

        self.date_label = QLabel()
        self.date_label.setStyleSheet(theme.style(
            "border: none; font-size: 11px; font-weight: 600; color: #2563eb;"))
        self.date_label.setVisible(bool(item.due_date))
        if item.due_date:
            self.date_label.setText(f"📅 {format_date_id(item.due_date)}")
        text_column.addWidget(self.date_label)
        layout.addLayout(text_column, 1)

        # Status dropdown. Block signals while populating so setCurrentIndex does
        # not fire a spurious change.
        self.status_combo = ComboBox()
        self.status_combo.setFixedWidth(150)
        for status in ACTION_STATUSES:
            self.status_combo.addItem(ACTION_STATUS_LABELS[status], status)
        self.status_combo.setCurrentIndex(
            ACTION_STATUSES.index(item.status)
            if item.status in ACTION_STATUSES else 0)
        self.status_combo.currentIndexChanged.connect(self._emit_status)
        layout.addWidget(self.status_combo, 0, Qt.AlignmentFlag.AlignVCenter)

        date_button = _link_button("📅")
        date_button.setToolTip("Ubah tanggal" if item.due_date else "Tambah tanggal")
        date_button.setFixedWidth(40)
        date_button.clicked.connect(
            lambda: self.date_edit_requested.emit(self.item_id))
        layout.addWidget(date_button, 0, Qt.AlignmentFlag.AlignVCenter)

        delete = DangerButton("Hapus")
        delete.setMinimumHeight(28)
        delete.clicked.connect(lambda: self.delete_requested.emit(self.item_id))
        layout.addWidget(delete, 0, Qt.AlignmentFlag.AlignVCenter)

    def _emit_status(self):
        self.status_changed.emit(self.item_id, self.status_combo.currentData())


class ActionItemsView(QWidget):
    """The action-item list + notes box for one shipment, rebuilt per shipment."""

    status_changed = pyqtSignal(str, str)     # item id, status
    add_requested = pyqtSignal()
    delete_requested = pyqtSignal(str)        # item id
    date_edit_requested = pyqtSignal(str)     # item id
    notes_edited = pyqtSignal(str)            # full notes text

    def __init__(self):
        super().__init__()
        self.rows: dict[str, ActionItemRow] = {}
        self._notes_original = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        heading = QHBoxLayout()
        title = QLabel("Item Tindakan")
        title.setStyleSheet(theme.style(
            "font-size: 14px; font-weight: 700; color: #111827;"))
        heading.addWidget(title)
        heading.addStretch(1)
        self.add_button = SecondaryButton("+  Tambah Item")
        self.add_button.clicked.connect(self.add_requested)
        heading.addWidget(self.add_button)
        layout.addLayout(heading)

        self.list_layout = QVBoxLayout()
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        layout.addLayout(self.list_layout)

        notes_label = QLabel("Catatan")
        notes_label.setStyleSheet(theme.style(
            "font-size: 13px; font-weight: 700; color: #111827; margin-top: 8px;"))
        layout.addWidget(notes_label)

        self.notes = QTextEdit()
        self.notes.setPlaceholderText("Catatan bebas untuk pengiriman ini…")
        self.notes.setFixedHeight(90)
        self.notes.setStyleSheet(theme.style(
            "background: white; border: 1px solid #e5e7eb; border-radius: 8px;"
            "font-size: 12px; color: #374151; padding: 6px;"))
        layout.addWidget(self.notes)

        save = QHBoxLayout()
        save.addStretch(1)
        self.save_notes_button = SecondaryButton("Simpan Catatan")
        self.save_notes_button.clicked.connect(self._commit_notes)
        save.addWidget(self.save_notes_button)
        layout.addLayout(save)

        layout.addStretch(1)

    def apply(self, items, notes: str | None):
        while self.list_layout.count():
            taken = self.list_layout.takeAt(0)
            if taken.widget() is not None:
                taken.widget().deleteLater()
        self.rows.clear()

        for item in items:
            row = ActionItemRow(item)
            row.status_changed.connect(self.status_changed)
            row.delete_requested.connect(self.delete_requested)
            row.date_edit_requested.connect(self.date_edit_requested)
            self.rows[item.id] = row
            self.list_layout.addWidget(row)

        self._notes_original = notes or ""
        self.notes.setPlainText(self._notes_original)

    def _commit_notes(self):
        text = self.notes.toPlainText().strip()
        if text != self._notes_original:
            self.notes_edited.emit(text)
            self._notes_original = text
