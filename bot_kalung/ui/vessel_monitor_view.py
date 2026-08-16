"""Monitor Kapal — watch vessels on BNCT across a rolling 3-voyage window.

A kanban board: each voyage sits in the column for its current BNCT state
(belum terjadwal → terjadwal → sudah sandar → sudah berangkat), sorted by vessel
name then voyage. Vessels are added and removed through the "Kelola Kapal"
dialog; the board itself is read-only. State lives in the DB, so the screen is
safe to rebuild on a theme change — `refresh()` reloads and re-buckets.
"""

from __future__ import annotations

import json

from . import theme

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
    QWidget,
)

from ..services.shipments import Shipments
from ..services.vessel_monitor import MonitoredVessels, state_of
from .bnct_display import describe_record
from .vessel_manager_dialog import VesselManagerDialog
from .widgets import FlowLayout, Panel, PrimaryButton, format_date_id

# (status key, column title, accent colour) — colours match describe_record.
COLUMNS = [
    ("notfound", "Belum Terjadwal", "#6b7280"),
    ("scheduled", "Terjadwal", "#2563eb"),
    ("berthed", "Sudah Sandar", "#059669"),
    ("departed", "Sudah Berangkat", "#b91c1c"),
]


def vessel_status(row) -> str:
    """Which board column a monitored voyage belongs in (its strict state)."""
    return state_of(row)


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
    """One monitored voyage as a kanban card. Its shipment chips are clickable."""

    shipment_clicked = pyqtSignal(str)     # shipment_id

    def __init__(self, row, accent: str, shipments_on_voyage=None):
        super().__init__()
        # Never let the card's own content force it wider than the column: it
        # takes whatever width the column gives and wraps inside (chips flow,
        # text word-wraps), so nothing spills off the right edge.
        self.setMinimumWidth(0)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored,
                             QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)   # let a column grow the card for wrapped chips
        self.setSizePolicy(policy)
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

        # {exporter}{seq} of any active shipment riding this voyage (live join),
        # each a chip that opens that shipment's detail page. A flow layout wraps
        # the chips onto new lines instead of letting them spill off the card.
        if shipments_on_voyage:
            chip_host = QWidget()
            chip_host.setStyleSheet(theme.style(
                "background: transparent; border: none;"))
            policy = chip_host.sizePolicy()
            policy.setHeightForWidth(True)
            chip_host.setSizePolicy(policy)
            flow = FlowLayout(chip_host)
            icon = QLabel("📦")
            icon.setStyleSheet(theme.style("border: none; font-size: 11px;"))
            flow.addWidget(icon)
            # Built from theme tokens so the chip is a proper accent pill in dark
            # mode too (fixed light-blue hex read as a light chip on a dark card).
            accent = theme.color("accent")
            chip_style = (
                f"QPushButton {{ border: 1px solid {accent};"
                f" background: {theme.color('accent_soft')}; color: {accent};"
                " border-radius: 4px; padding: 1px 8px; font-size: 11px;"
                " font-weight: 700; }"
                f"QPushButton:hover {{ background: {accent}; color: #ffffff; }}")
            for label, shipment_id in shipments_on_voyage:
                chip = QPushButton(label)
                chip.setCursor(Qt.CursorShape.PointingHandCursor)
                chip.setStyleSheet(chip_style)
                chip.clicked.connect(
                    lambda _, sid=shipment_id: self.shipment_clicked.emit(sid))
                flow.addWidget(chip)
            outer.addWidget(chip_host)

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


class VesselMonitorView(QWidget):
    """The standalone vessel-monitoring board."""

    vessel_added = pyqtSignal()                # vessels changed; trigger a poll
    shipment_opened = pyqtSignal(str)          # a voyage chip was clicked

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.vessels = MonitoredVessels(db)
        self.shipments = Shipments(db)
        self.columns: dict[str, dict] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Monitor Kapal")
        title.setStyleSheet(theme.style(
            "font-size: 22px; font-weight: 700; color: #111827;"))
        header.addWidget(title)
        header.addStretch(1)
        manage = PrimaryButton("Kelola Kapal")
        manage.clicked.connect(self._open_manager)
        header.addWidget(manage)
        outer.addLayout(header)

        subtitle = QLabel(
            "Pantau kapal di BNCT — tiap kapal dipantau untuk 3 voyage ke depan. "
            "Notifikasi sama seperti pemantauan pengiriman.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(theme.style("font-size: 12px; color: #6b7280;"))
        outer.addWidget(subtitle)

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
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # never spill sideways
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
                matches = self.shipments.for_voyage(row["vessel_name"], row["voyage"])
                pairs = [(f"{m['exporter_code']}{m['sequence_number']}", m["id"])
                         for m in matches]
                card = VesselCard(row, column["color"], pairs)
                card.shipment_clicked.connect(self.shipment_opened)
                layout.insertWidget(layout.count() - 1, card)

            column["header"].setText(f"{column['title']} ({len(rows)})")

    def _open_manager(self):
        dialog = VesselManagerDialog(self.db, self)
        dialog.changed.connect(self._on_managed)
        dialog.exec()

    def _on_managed(self):
        self.refresh()
        self.vessel_added.emit()               # poll BNCT for any new voyages
