"""In-app notification centre (2026-07-21).

Lists persisted notifications newest-first, with an unread accent. Clicking one
opens its shipment and marks it read; a button marks them all read. Backs the
sidebar's unread counter, so notifications outlive the few seconds a Windows
tray toast survives.
"""

from __future__ import annotations

from . import theme

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from ..services.notifications import NotificationStore
from .widgets import InlineMessage, Panel, SecondaryButton, format_date_id

# Accent per notification kind (matches the BNCT panel's colours).
KIND_COLOR = {"schedule": "#2563eb", "alongside": "#059669",
              "departing": "#b91c1c", "container": "#d97706"}


def _when(iso: str) -> str:
    if not iso or "T" not in iso:
        return format_date_id(iso)
    date, time = iso.split("T", 1)
    return f"{format_date_id(date)} {time[:5]}"


class NotificationCard(Panel):
    clicked = pyqtSignal(str, object)   # (notification id, shipment id)

    def __init__(self, note):
        super().__init__()
        self._note = note
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        unread = not note.read
        bg = "#eff6ff" if unread else "#ffffff"
        self.setStyleSheet(theme.style(
            f"background: {bg}; border: 1px solid #e5e7eb; border-radius: 8px;"))

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)

        dot = QLabel("●")
        dot.setStyleSheet(theme.style(
            f"color: {KIND_COLOR.get(note.kind, '#6b7280')}; border: none;"
            f"font-size: 14px;"))
        dot.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(dot)

        body = QVBoxLayout()
        body.setSpacing(2)
        title = QLabel(note.title)
        title.setWordWrap(True)
        title.setStyleSheet(theme.style(
            "border: none; font-size: 13px; color: #111827;"
            f"font-weight: {'700' if unread else '600'};"))
        body.addWidget(title)

        if note.body:
            detail = QLabel(note.body)
            detail.setWordWrap(True)
            detail.setStyleSheet(theme.style(
                "border: none; font-size: 12px; color: #4b5563;"))
            body.addWidget(detail)

        when = QLabel(_when(note.created_at) + ("" if note.read else "  •  baru"))
        when.setStyleSheet(theme.style(
            "border: none; font-size: 11px; color: #9ca3af;"))
        body.addWidget(when)
        row.addLayout(body, 1)

    def mousePressEvent(self, event):
        self.clicked.emit(self._note.id, self._note.shipment_id)


class NotificationsView(QWidget):
    open_shipment_requested = pyqtSignal(str)
    open_vessel_monitor_requested = pyqtSignal()   # a shipment-less vessel alert
    changed = pyqtSignal()          # read-state changed; refresh the badge

    def __init__(self, db):
        super().__init__()
        self.store = NotificationStore(db)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Notifikasi")
        title.setStyleSheet(theme.style(
            "font-size: 22px; font-weight: 700; color: #111827;"))
        header.addWidget(title)
        header.addStretch(1)
        self.mark_all_button = SecondaryButton("Tandai semua dibaca")
        self.mark_all_button.clicked.connect(self._mark_all_read)
        header.addWidget(self.mark_all_button)
        outer.addLayout(header)

        self.message = InlineMessage()
        outer.addWidget(self.message)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.container = QWidget()
        self.list_layout = QVBoxLayout(self.container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.container)
        outer.addWidget(self.scroll, 1)

        self.empty_note = QLabel("Belum ada notifikasi.")
        self.empty_note.setStyleSheet(theme.style(
            "font-size: 13px; color: #9ca3af; padding: 8px;"))
        outer.addWidget(self.empty_note)

    def refresh(self):
        # Clear existing cards (keep the trailing stretch at the end).
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        notes = self.store.recent()
        for note in notes:
            card = NotificationCard(note)
            card.clicked.connect(self._on_card_clicked)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)

        self.empty_note.setVisible(not notes)
        self.scroll.setVisible(bool(notes))
        self.mark_all_button.setEnabled(any(not n.read for n in notes))

    def _on_card_clicked(self, note_id: str, shipment_id):
        self.store.mark_read(note_id)
        self.changed.emit()
        self.refresh()
        if shipment_id:
            self.open_shipment_requested.emit(shipment_id)
        else:
            self.open_vessel_monitor_requested.emit()

    def _mark_all_read(self):
        self.store.mark_all_read()
        self.message.show_success("Semua notifikasi ditandai dibaca.")
        self.changed.emit()
        self.refresh()
