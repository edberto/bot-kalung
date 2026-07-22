"""Activity log — the shipment lifecycle audit trail (2026-07-21).

A passive, read-only record of who created, completed or deleted a shipment on
the shared database. Deliberately does not drive the unread badge: it is a log,
not an alert.
"""

from __future__ import annotations

from . import theme

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..services.audit import ACTION_COLORS, AuditLog
from .widgets import Panel, format_date_id


def _who(email: str | None) -> str:
    if not email:
        return "tidak diketahui"
    return email.split("@", 1)[0]


def _when(iso: str) -> str:
    if not iso or "T" not in iso:
        return format_date_id(iso)
    date, time = iso.split("T", 1)
    return f"{format_date_id(date)} {time[:5]}"


class AuditCard(Panel):
    """One log entry."""

    def __init__(self, entry):
        super().__init__()
        self.setStyleSheet(theme.style(
            "background: white; border: 1px solid #e5e7eb; border-radius: 8px;"))
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)

        dot = QLabel("●")
        dot.setStyleSheet(theme.style(
            f"color: {ACTION_COLORS.get(entry.action, '#6b7280')};"
            "border: none; font-size: 14px;"))
        dot.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(dot)

        body = QVBoxLayout()
        body.setSpacing(2)

        headline = QLabel(f"{entry.action_label} — {entry.label}"
                          if entry.label else entry.action_label)
        headline.setWordWrap(True)
        headline.setStyleSheet(theme.style(
            "border: none; font-size: 13px; font-weight: 600; color: #111827;"))
        body.addWidget(headline)

        if entry.detail:
            detail = QLabel(entry.detail)
            detail.setWordWrap(True)
            detail.setTextFormat(Qt.TextFormat.PlainText)
            detail.setStyleSheet(theme.style(
                "border: none; font-size: 12px; color: #4b5563;"))
            body.addWidget(detail)

        meta = QLabel(f"oleh {_who(entry.actor)} · {_when(entry.created_at)}")
        meta.setStyleSheet(theme.style(
            "border: none; font-size: 11px; color: #9ca3af;"))
        body.addWidget(meta)

        row.addLayout(body, 1)


class AuditView(QWidget):
    """The activity log screen."""

    def __init__(self, db):
        super().__init__()
        self.log = AuditLog(db)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(12)

        title = QLabel("Log Aktivitas")
        title.setStyleSheet(theme.style(
            "font-size: 22px; font-weight: 700; color: #111827;"))
        outer.addWidget(title)

        subtitle = QLabel(
            "Catatan pengiriman yang dibuat, ditandai selesai, atau dihapus — "
            "oleh siapa dan kapan.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(theme.style("font-size: 12px; color: #6b7280;"))
        outer.addWidget(subtitle)

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

        self.empty_note = QLabel("Belum ada aktivitas.")
        self.empty_note.setStyleSheet(theme.style(
            "font-size: 13px; color: #9ca3af; padding: 8px;"))
        outer.addWidget(self.empty_note)

    def refresh(self):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        entries = self.log.recent()
        for entry in entries:
            self.list_layout.insertWidget(
                self.list_layout.count() - 1, AuditCard(entry))

        self.empty_note.setVisible(not entries)
        self.scroll.setVisible(bool(entries))
