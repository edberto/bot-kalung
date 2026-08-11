"""Manage the vessels monitored on the Monitor Kapal board (2026-08-07).

Add a vessel from one starting voyage (the app then rolls a 3-voyage window),
delete a whole vessel, or delete a single voyage. Emits `changed` after every
mutation so the board refreshes and polls BNCT.
"""

from __future__ import annotations

from . import theme

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget,
)

from ..services.vessel_monitor import MonitoredVessels, state_of
from .widgets import DangerButton, InlineMessage, Panel, PrimaryButton, SecondaryButton

STATE_LABELS = {
    "notfound": "Belum Terjadwal",
    "scheduled": "Terjadwal",
    "berthed": "Sudah Sandar",
    "departed": "Sudah Berangkat",
}
STATE_COLORS = {
    "notfound": "#6b7280", "scheduled": "#2563eb",
    "berthed": "#059669", "departed": "#b91c1c",
}


class VesselManagerDialog(QDialog):
    changed = pyqtSignal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.vessels = MonitoredVessels(db)

        self.setWindowTitle("Kelola Kapal Dipantau")
        self.setMinimumSize(560, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)

        intro = QLabel(
            "Tiap kapal dipantau untuk 3 voyage ke depan. Nomor voyage bertambah "
            "otomatis (mis. N379 → N380 → N381); saat satu voyage berangkat, "
            "voyage berikutnya ditambahkan sendiri.")
        intro.setWordWrap(True)
        intro.setStyleSheet(theme.style("font-size: 12px; color: #6b7280;"))
        outer.addWidget(intro)

        # -- add row ---------------------------------------------------------
        form = QHBoxLayout()
        form.setSpacing(8)
        self.name_field = QLineEdit()
        self.name_field.setPlaceholderText("Nama kapal")
        self.name_field.setMinimumHeight(34)
        self.voyage_field = QLineEdit()
        self.voyage_field.setPlaceholderText("Voyage awal (mis. N379)")
        self.voyage_field.setMinimumHeight(34)
        self.voyage_field.setMaximumWidth(190)
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
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.container)
        outer.addWidget(self.scroll, 1)

        self.empty_note = QLabel("Belum ada kapal yang dipantau.")
        self.empty_note.setStyleSheet(theme.style(
            "font-size: 13px; color: #9ca3af; padding: 6px;"))
        outer.addWidget(self.empty_note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = SecondaryButton("Tutup")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        outer.addLayout(buttons)

        self.refresh()

    # -- data ---------------------------------------------------------------

    def refresh(self):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        groups = self.vessels.groups()
        for name, rows in groups.items():
            self.list_layout.insertWidget(
                self.list_layout.count() - 1, self._vessel_block(name, rows))
        self.empty_note.setVisible(not groups)
        self.scroll.setVisible(bool(groups))

    def _vessel_block(self, name: str, rows: list) -> QWidget:
        block = Panel()
        block.setStyleSheet(theme.style(
            "background: white; border: 1px solid #e5e7eb; border-radius: 8px;"))
        layout = QVBoxLayout(block)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel(name)
        title.setStyleSheet(theme.style(
            "border: none; font-size: 14px; font-weight: 700; color: #0f172a;"))
        head.addWidget(title, 1)
        del_vessel = DangerButton("Hapus Kapal")
        del_vessel.setMinimumHeight(26)
        del_vessel.clicked.connect(lambda: self._delete_vessel(name))
        head.addWidget(del_vessel)
        layout.addLayout(head)

        for row in rows:
            layout.addLayout(self._voyage_row(name, row))
        return block

    def _voyage_row(self, name: str, row) -> QHBoxLayout:
        line = QHBoxLayout()
        line.setSpacing(8)
        voyage = QLabel(row["voyage"] or "(tanpa voyage)")
        voyage.setStyleSheet(theme.style(
            "border: none; font-size: 13px; color: #111827;"))
        line.addWidget(voyage)

        state = state_of(row)
        badge = QLabel(STATE_LABELS.get(state, state))
        badge.setStyleSheet(theme.style(
            f"border: none; font-size: 11px; font-weight: 600; "
            f"color: {STATE_COLORS.get(state, '#6b7280')};"))
        line.addWidget(badge)
        line.addStretch(1)

        remove = DangerButton("Hapus")
        remove.setMinimumHeight(24)
        remove.clicked.connect(lambda: self._delete_voyage(name, row["id"]))
        line.addWidget(remove)
        return line

    # -- mutations ----------------------------------------------------------

    def _add(self):
        name = self.name_field.text().strip()
        voyage = self.voyage_field.text().strip()
        if not name or not voyage:
            self.message.show_error("Isi nama kapal dan voyage awal.")
            return
        if not self._safely(lambda: self.vessels.add_vessel(name, voyage)):
            return
        self.name_field.clear()
        self.voyage_field.clear()
        self.message.show_success(f"Memantau {name} mulai voyage {voyage}.")
        self.refresh()
        self.changed.emit()

    def _delete_voyage(self, name: str, vessel_id: str):
        def run():
            self.vessels.delete(vessel_id)
            self.vessels.ensure_window(name)   # refill if a live voyage was removed
        if not self._safely(run):
            return
        self.refresh()
        self.changed.emit()

    def _delete_vessel(self, name: str):
        if not self._safely(lambda: self.vessels.delete_vessel(name)):
            return
        self.message.show_info(f"{name} dihapus dari pemantauan.")
        self.refresh()
        self.changed.emit()

    def _safely(self, action) -> bool:
        """Run a DB mutation without letting an error escape the Qt slot."""
        try:
            action()
            return True
        except Exception as exc:      # noqa: BLE001 - surfaced, never crashes
            self.message.show_error(f"Gagal menyimpan: {exc}")
            return False
