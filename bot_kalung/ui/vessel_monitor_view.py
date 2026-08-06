"""Monitor Kapal — watch any vessel on BNCT by name + voyage, with no shipment.

Uses the same poll and alerts as shipment monitoring (schedule → alongside →
departing); the shared BNCT controller reads these targets alongside the active
shipments. State lives entirely in the DB, so the screen is safe to rebuild on a
theme change — `refresh()` reloads everything.
"""

from __future__ import annotations

from . import theme

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget,
)

from ..services.vessel_monitor import MonitoredVessels
from .widgets import DangerButton, InlineMessage, Panel, PrimaryButton, format_date_id


def _when(iso: str | None) -> str:
    if not iso:
        return "Belum diperiksa"
    if "T" not in iso:
        return format_date_id(iso)
    date, time = iso.split("T", 1)
    return f"{format_date_id(date)} {time[:5]}"


def _phase_color(row) -> str:
    if row["last_departing"]:
        return "#dc2626"                       # red — pay LOLO
    if row["last_phase"] == "alongside":
        return "#d97706"                       # amber — berthed
    if row["last_found"]:
        return "#2563eb"                       # blue — scheduled
    return "#9ca3af"                           # grey — not seen yet


class VesselCard(Panel):
    """One monitored vessel with its latest status."""

    remove_requested = pyqtSignal(str)         # monitored-vessel id

    def __init__(self, row):
        super().__init__()
        self._id = row["id"]
        self.setStyleSheet(theme.style(
            "background: white; border: 1px solid #e5e7eb; border-radius: 8px;"))
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(10)

        dot = QLabel("●")
        dot.setStyleSheet(theme.style(
            f"color: {_phase_color(row)}; border: none; font-size: 14px;"))
        dot.setAlignment(Qt.AlignmentFlag.AlignTop)
        outer.addWidget(dot)

        body = QVBoxLayout()
        body.setSpacing(2)

        name = row["vessel_name"]
        if row["voyage"]:
            name = f"{name} · {row['voyage']}"
        headline = QLabel(name)
        headline.setWordWrap(True)
        headline.setStyleSheet(theme.style(
            "border: none; font-size: 13px; font-weight: 600; color: #111827;"))
        body.addWidget(headline)

        status = QLabel(row["last_summary"] or "Menunggu pemeriksaan pertama…")
        status.setWordWrap(True)
        status.setTextFormat(Qt.TextFormat.PlainText)
        status.setStyleSheet(theme.style(
            "border: none; font-size: 12px; color: #4b5563;"))
        body.addWidget(status)

        meta = QLabel(f"Diperiksa: {_when(row['last_checked_at'])}")
        meta.setStyleSheet(theme.style(
            "border: none; font-size: 11px; color: #9ca3af;"))
        body.addWidget(meta)

        outer.addLayout(body, 1)

        remove = DangerButton("Hapus")
        remove.clicked.connect(lambda: self.remove_requested.emit(self._id))
        outer.addWidget(remove, 0, Qt.AlignmentFlag.AlignTop)


class VesselMonitorView(QWidget):
    """The standalone vessel-monitoring screen."""

    vessel_added = pyqtSignal()                # a vessel was added; trigger a poll

    def __init__(self, db):
        super().__init__()
        self.vessels = MonitoredVessels(db)

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

        # -- list ------------------------------------------------------------
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

        self.empty_note = QLabel("Belum ada kapal yang dipantau.")
        self.empty_note.setStyleSheet(theme.style(
            "font-size: 13px; color: #9ca3af; padding: 8px;"))
        outer.addWidget(self.empty_note)

        self.refresh()

    # -- data ---------------------------------------------------------------

    def refresh(self):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        rows = self.vessels.all()
        for row in rows:
            card = VesselCard(row)
            card.remove_requested.connect(self._remove)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)

        self.empty_note.setVisible(not rows)
        self.scroll.setVisible(bool(rows))

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
