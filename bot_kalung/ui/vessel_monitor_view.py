"""Monitor Kapal — watch any vessel on BNCT by name + voyage, with no shipment.

A kanban board: each vessel sits in the column for its current BNCT state
(belum terjadwal → terjadwal → sudah sandar → sudah berangkat), sorted by
vessel name then voyage. State lives entirely in the DB, so the screen is safe
to rebuild on a theme change — `refresh()` reloads and re-buckets everything.
"""

from __future__ import annotations

import json

from . import theme

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget,
)

from ..services.vessel_monitor import MonitoredVessels
from .bnct_display import describe_record
from .widgets import DangerButton, InlineMessage, Panel, PrimaryButton, format_date_id

# (status key, column title, accent colour) — colours match describe_record.
COLUMNS = [
    ("notfound", "Belum Terjadwal", "#6b7280"),
    ("scheduled", "Terjadwal", "#2563eb"),
    ("berthed", "Sudah Sandar", "#059669"),
    ("departed", "Sudah Berangkat", "#b91c1c"),
]


def vessel_status(row) -> str:
    """Which board column a monitored vessel belongs in."""
    if row["last_departing"]:
        return "departed"
    if row["last_phase"] == "alongside":
        return "berthed"
    if row["last_found"]:
        return "scheduled"
    return "notfound"


def _when(iso: str | None) -> str:
    if not iso:
        return "Belum diperiksa"
    if "T" not in iso:
        return format_date_id(iso)
    date, time = iso.split("T", 1)
    return f"{format_date_id(date)} {time[:5]}"


def _reading(row):
    try:
        raw = row["last_reading"]
    except (KeyError, IndexError):
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


class VesselCard(Panel):
    """One monitored vessel as a kanban card."""

    remove_requested = pyqtSignal(str)         # monitored-vessel id

    def __init__(self, row, accent: str):
        super().__init__()
        self._id = row["id"]
        self.setStyleSheet(theme.style(
            "background: white; border: 1px solid #e5e7eb; "
            f"border-left: 4px solid {accent}; border-radius: 8px;"))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(5)

        name = row["vessel_name"]
        if row["voyage"]:
            name = f"{name} · {row['voyage']}"
        headline = QLabel(name)
        headline.setWordWrap(True)
        headline.setStyleSheet(theme.style(
            "border: none; font-size: 14px; font-weight: 700; color: #0f172a;"))
        outer.addWidget(headline)

        record = _reading(row)
        detail = describe_record(record)[2] if record is not None else ""
        if detail:
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            detail_label.setTextFormat(Qt.TextFormat.PlainText)
            detail_label.setStyleSheet(theme.style(
                "border: none; font-size: 12px; color: #374151;"))
            outer.addWidget(detail_label)

        meta = QLabel(f"Diperiksa: {_when(row['last_checked_at'])}")
        meta.setStyleSheet(theme.style(
            "border: none; font-size: 11px; color: #9ca3af;"))
        outer.addWidget(meta)

        actions = QHBoxLayout()
        actions.addStretch(1)
        remove = DangerButton("Hapus")
        remove.setMinimumHeight(26)
        remove.clicked.connect(lambda: self.remove_requested.emit(self._id))
        actions.addWidget(remove)
        outer.addLayout(actions)


class VesselMonitorView(QWidget):
    """The standalone vessel-monitoring board."""

    vessel_added = pyqtSignal()                # a vessel was added; trigger a poll

    def __init__(self, db):
        super().__init__()
        self.vessels = MonitoredVessels(db)
        self.columns: dict[str, dict] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(12)

        title = QLabel("Monitor Kapal")
        title.setStyleSheet(theme.style(
            "font-size: 22px; font-weight: 700; color: #111827;"))
        outer.addWidget(title)

        subtitle = QLabel(
            "Pantau kapal di BNCT dengan nama dan voyage, tanpa perlu terkait "
            "pengiriman. Notifikasi sama seperti pemantauan pengiriman.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(theme.style("font-size: 12px; color: #6b7280;"))
        outer.addWidget(subtitle)

        # -- add form --------------------------------------------------------
        form = QHBoxLayout()
        form.setSpacing(8)
        self.name_field = QLineEdit()
        self.name_field.setPlaceholderText("Nama kapal")
        self.name_field.setMinimumHeight(34)
        self.voyage_field = QLineEdit()
        self.voyage_field.setPlaceholderText("Voyage")
        self.voyage_field.setMinimumHeight(34)
        self.voyage_field.setMaximumWidth(160)
        add_button = PrimaryButton("Tambah")
        add_button.clicked.connect(self._add)
        self.name_field.returnPressed.connect(self._add)
        self.voyage_field.returnPressed.connect(self._add)
        form.addWidget(self.name_field, 1)
        form.addWidget(self.voyage_field)
        form.addWidget(add_button)
        outer.addLayout(form)

        self.message = InlineMessage()
        outer.addWidget(self.message)

        # -- board -----------------------------------------------------------
        board = QHBoxLayout()
        board.setSpacing(12)
        for key, col_title, color in COLUMNS:
            board.addWidget(self._build_column(key, col_title, color), 1)
        outer.addLayout(board, 1)

        self.refresh()

    def _build_column(self, key: str, col_title: str, color: str) -> QWidget:
        column = QWidget()
        column.setStyleSheet(theme.style(
            "background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 10px;"))
        col_layout = QVBoxLayout(column)
        col_layout.setContentsMargins(10, 10, 10, 10)
        col_layout.setSpacing(8)

        header = QLabel(col_title)
        header.setStyleSheet(theme.style(
            f"border: none; font-size: 13px; font-weight: 700; color: {color};"))
        col_layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(theme.style("background: transparent; border: none;"))
        container = QWidget()
        container.setStyleSheet(theme.style("background: transparent;"))
        list_layout = QVBoxLayout(container)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(8)
        list_layout.addStretch(1)
        scroll.setWidget(container)
        col_layout.addWidget(scroll, 1)

        self.columns[key] = {"header": header, "title": col_title, "color": color,
                             "layout": list_layout, "container": container}
        return column

    # -- data ---------------------------------------------------------------

    def refresh(self):
        buckets: dict[str, list] = {key: [] for key, _, _ in COLUMNS}
        for row in self.vessels.all():
            buckets[vessel_status(row)].append(row)

        for key, column in self.columns.items():
            layout = column["layout"]
            while layout.count() > 1:            # keep the trailing stretch
                item = layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()

            rows = sorted(
                buckets[key],
                key=lambda r: ((r["vessel_name"] or "").lower(),
                               (r["voyage"] or "").lower()))
            column["rows"] = rows                # ordered, for tests/inspection
            for row in rows:
                card = VesselCard(row, column["color"])
                card.remove_requested.connect(self._remove)
                layout.insertWidget(layout.count() - 1, card)

            column["header"].setText(f"{column['title']} ({len(rows)})")

    def _add(self):
        name = self.name_field.text().strip()
        if not name:
            self.message.show_error("Isi nama kapal terlebih dahulu.")
            return
        self.vessels.add(name, self.voyage_field.text().strip())
        self.name_field.clear()
        self.voyage_field.clear()
        self.message.show_info(f"Memantau {name}. Memeriksa BNCT…")
        self.refresh()
        self.vessel_added.emit()

    def _remove(self, vessel_id: str):
        self.vessels.delete(vessel_id)
        self.refresh()
