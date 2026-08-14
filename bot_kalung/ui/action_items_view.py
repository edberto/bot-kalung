"""Action items and notes for a scanned shipment.

Each is a distinct card. Action items are one row apiece — title, a status
dropdown (colour-coded by state), and a subtle delete that confirms before
removing. Notes is a free-text box beside the list.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout,
)

from . import theme
from ..core.constants import ACTION_STATUS_LABELS, ACTION_STATUSES
from .widgets import CARD_STYLE, ComboBox, Panel, SecondaryButton, section_header

# Colour per status, from "not started" grey to "final" green.
_STATUS_COLOR = {
    "pending": "#9ca3af",
    "in_progress": "#2563eb",
    "draft_received": "#7c3aed",
    "draft_revision": "#d97706",
    "draft_ok": "#0891b2",
    "final": "#16a34a",
}


class _StatusCombo(ComboBox):
    """A status dropdown that reads as a modern pill: its current value is
    centred and tinted by the status colour, and clicking anywhere opens it.

    A non-editable QComboBox can't centre its collapsed text, so it is made
    editable with a read-only, centred line edit standing in for the display.
    """

    def __init__(self):
        super().__init__()
        self.setEditable(True)
        edit = self.lineEdit()
        edit.setReadOnly(True)
        edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        edit.setCursor(Qt.CursorShape.PointingHandCursor)
        edit.setStyleSheet("border: none; background: transparent;")
        edit.installEventFilter(self)          # click the field, not just the arrow

        for status in ACTION_STATUSES:
            self.addItem(ACTION_STATUS_LABELS[status], status)
        for index in range(self.count()):       # centre the popup rows too
            self.setItemData(index, Qt.AlignmentFlag.AlignCenter,
                             Qt.ItemDataRole.TextAlignmentRole)

    def eventFilter(self, obj, event):
        if obj is self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress:
            self.showPopup()
            return True
        return super().eventFilter(obj, event)

    def apply_color(self, color: str):
        """Tint the pill to the current status' colour."""
        self.setStyleSheet(theme.style(f"""
            QComboBox {{ background: #ffffff; border: 1px solid #e5e7eb;
                border-radius: 8px; padding: 5px 8px; font-size: 12px;
                font-weight: 700; color: {color}; }}
            QComboBox:hover {{ border-color: {color}; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox::down-arrow {{ width: 10px; height: 10px; }}
            QComboBox QAbstractItemView {{ border: 1px solid #e5e7eb;
                border-radius: 8px; background: #ffffff; padding: 0;
                outline: none; }}
            QComboBox QAbstractItemView::item {{ min-height: 28px;
                padding: 5px 12px; border: none; color: #111827; }}
            QComboBox QAbstractItemView::item:hover {{ background: #f3f4f6; }}
            QComboBox QAbstractItemView::item:selected {{ background: #eef2ff;
                color: #1d4ed8; }}
        """))


class ActionItemRow(Panel):
    """One action item: a title, a status dropdown, and a subtle delete."""

    status_changed = pyqtSignal(str, str)     # item id, status
    delete_requested = pyqtSignal(str)        # item id

    def __init__(self, item):
        super().__init__()
        self.item_id = item.id
        color = _STATUS_COLOR.get(item.status, "#9ca3af")
        self.setStyleSheet(theme.style(
            "background: #f9fafb; border: 1px solid #e5e7eb;"
            f"border-left: 3px solid {color}; border-radius: 8px;"))

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 9, 10, 9)
        row.setSpacing(10)

        title = QLabel(item.title + ("  (tambahan)" if item.is_custom else ""))
        title.setWordWrap(True)
        title.setStyleSheet(theme.style(
            "border: none; background: transparent; font-size: 13px;"
            "font-weight: 600; color: #111827;"))
        row.addWidget(title, 1)

        self.status_combo = _StatusCombo()
        self.status_combo.setFixedWidth(150)
        self.status_combo.setCurrentIndex(
            ACTION_STATUSES.index(item.status)
            if item.status in ACTION_STATUSES else 0)
        self.status_combo.apply_color(color)
        self.status_combo.currentIndexChanged.connect(self._on_status_picked)
        row.addWidget(self.status_combo, 0, Qt.AlignmentFlag.AlignVCenter)

        delete = QPushButton("✕")
        delete.setCursor(Qt.CursorShape.PointingHandCursor)
        delete.setFixedSize(26, 26)
        delete.setToolTip("Hapus item")
        delete.setStyleSheet(theme.style("""
            QPushButton { border: none; background: transparent; color: #9ca3af;
                          font-size: 14px; border-radius: 6px; }
            QPushButton:hover { background: #fef2f2; color: #dc2626; }
        """))
        delete.clicked.connect(lambda: self.delete_requested.emit(self.item_id))
        row.addWidget(delete, 0, Qt.AlignmentFlag.AlignVCenter)

    def _on_status_picked(self):
        status = self.status_combo.currentData()
        # Retint the pill and the row's accent in place so the new state reads
        # instantly — the list is no longer rebuilt on a status change.
        color = _STATUS_COLOR.get(status, "#9ca3af")
        self.setStyleSheet(theme.style(
            "background: #f9fafb; border: 1px solid #e5e7eb;"
            f"border-left: 3px solid {color}; border-radius: 8px;"))
        self.status_combo.apply_color(color)
        self.status_changed.emit(self.item_id, status)

    def current_status(self) -> str:
        return self.status_combo.currentData()


class ActionItemsView(Panel):
    """The action-item list card for one shipment, rebuilt per shipment."""

    status_changed = pyqtSignal(str, str)     # item id, status
    add_requested = pyqtSignal()
    delete_requested = pyqtSignal(str)        # item id

    def __init__(self):
        super().__init__()
        self.rows: dict[str, ActionItemRow] = {}
        self.setStyleSheet(theme.style(CARD_STYLE))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        heading.addWidget(section_header("Item Tindakan"))
        heading.addStretch(1)
        self.add_button = SecondaryButton("+  Tambah Item")
        self.add_button.clicked.connect(self.add_requested)
        heading.addWidget(self.add_button)
        layout.addLayout(heading)

        self.list_layout = QVBoxLayout()
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        layout.addLayout(self.list_layout)
        layout.addStretch(1)

    def apply(self, items):
        while self.list_layout.count():
            taken = self.list_layout.takeAt(0)
            if taken.widget() is not None:
                taken.widget().deleteLater()
        self.rows.clear()

        for item in items:
            row = ActionItemRow(item)
            row.status_changed.connect(self.status_changed)
            row.delete_requested.connect(self.delete_requested)
            self.rows[item.id] = row
            self.list_layout.addWidget(row)


class NotesPanel(Panel):
    """The shipment's free-text notes card, saved on button press."""

    notes_edited = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._original = ""
        self.setStyleSheet(theme.style(CARD_STYLE))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(section_header("Catatan"))

        self.notes = QTextEdit()
        self.notes.setPlaceholderText("Catatan bebas untuk pengiriman ini…")
        self.notes.setMinimumHeight(150)
        self.notes.setStyleSheet(theme.style(
            "background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px;"
            "font-size: 13px; color: #374151; padding: 8px;"))
        layout.addWidget(self.notes, 1)

        save = QHBoxLayout()
        save.addStretch(1)
        self.save_button = SecondaryButton("Simpan Catatan")
        self.save_button.clicked.connect(self._commit)
        save.addWidget(self.save_button)
        layout.addLayout(save)

    def set_notes(self, notes: str | None):
        self._original = notes or ""
        self.notes.setPlainText(self._original)

    def _commit(self):
        text = self.notes.toPlainText().strip()
        if text != self._original:
            self.notes_edited.emit(text)
            self._original = text
