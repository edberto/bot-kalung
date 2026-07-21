"""Shipment History (PRD Section 12).

Completed shipments, searchable and filterable, with a link back to the folder
on Drive. The app stores metadata only — the documents stay in Google Drive, so
a folder that has been moved or deleted reports an error rather than failing
silently.
"""

from __future__ import annotations

from . import theme

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from ..core.constants import EXPORTER_COLORS, EXPORTERS
from ..services import fileops
from ..services.shipments import Shipments
from .widgets import ComboBox, InlineMessage, format_date_id

COLUMNS = ["Eksportir", "No.", "Kapal / Voyage", "Booking", "Tujuan",
           "ETD", "Selesai", ""]


class SortableItem(QTableWidgetItem):
    """Sorts on a hidden key so dates order chronologically, not as text."""

    def __init__(self, text: str, key=None):
        super().__init__(text)
        self.setData(Qt.ItemDataRole.UserRole, key if key is not None else text)
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def __lt__(self, other):
        if isinstance(other, SortableItem):
            mine = self.data(Qt.ItemDataRole.UserRole)
            theirs = other.data(Qt.ItemDataRole.UserRole)
            try:
                return mine < theirs
            except TypeError:
                return str(mine) < str(theirs)
        return super().__lt__(other)


class FilterChip(QPushButton):
    """A toggleable exporter filter."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restyle()
        self.toggled.connect(lambda _: self._restyle())

    def _restyle(self):
        color = EXPORTER_COLORS.get(self.code, "#6b7280")
        if self.isChecked():
            self.setStyleSheet(theme.style(
                f"background: {color}; color: white; border: 1px solid {color};"
                "border-radius: 12px; padding: 4px 12px; font-size: 11px;"
                "font-weight: 700;"))
        else:
            self.setStyleSheet(theme.style(
                f"background: white; color: {color}; border: 1px solid #d1d5db;"
                "border-radius: 12px; padding: 4px 12px; font-size: 11px;"
                "font-weight: 700;"))


class HistoryView(QWidget):
    open_folder_requested = pyqtSignal(str)
    changed = pyqtSignal()          # a shipment was deleted; refresh stats

    def __init__(self, db):
        super().__init__()
        self.shipments = Shipments(db)
        self.rows: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Riwayat")
        title.setStyleSheet(theme.style("font-size: 22px; font-weight: 700; color: #111827;"))
        header.addWidget(title)
        header.addStretch(1)
        self.count_label = QLabel()
        self.count_label.setStyleSheet(theme.style("font-size: 12px; color: #6b7280;"))
        header.addWidget(self.count_label)
        outer.addLayout(header)

        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Cari eksportir, kapal, nomor booking, atau tujuan...")
        self.search.setMinimumHeight(34)
        self.search.textChanged.connect(self._apply_filters)
        outer.addWidget(self.search)

        filters = QHBoxLayout()
        filters.setSpacing(6)
        self.chips: list[FilterChip] = []
        for code in EXPORTERS:
            chip = FilterChip(code)
            chip.toggled.connect(self._apply_filters)
            self.chips.append(chip)
            filters.addWidget(chip)
        filters.addStretch(1)
        filters.addWidget(QLabel("Tahun"))
        self.year_combo = ComboBox()
        self.year_combo.currentIndexChanged.connect(self._apply_filters)
        filters.addWidget(self.year_combo)
        outer.addLayout(filters)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setStyleSheet(theme.style("""
            QTableWidget { border: 1px solid #e5e7eb; border-radius: 8px;
                           background: white; gridline-color: #f3f4f6; }
            QHeaderView::section { background: #f9fafb; border: none;
                                   border-bottom: 1px solid #e5e7eb;
                                   padding: 8px; font-weight: 600;
                                   color: #374151; }
        """))
        # Vessel and destination absorb the spare width; everything else sizes
        # to its content so "EVER CONCERT 0800-088N" is not truncated.
        head = self.table.horizontalHeader()
        for index in range(len(COLUMNS)):
            mode = (QHeaderView.ResizeMode.Stretch if index in (2, 4)
                    else QHeaderView.ResizeMode.ResizeToContents)
            head.setSectionResizeMode(index, mode)
        outer.addWidget(self.table, 1)

        self.empty_state = QLabel("Belum ada pengiriman yang selesai.")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setStyleSheet(theme.style("font-size: 14px; color: #9ca3af;"))
        outer.addWidget(self.empty_state)

        self.message = InlineMessage()
        outer.addWidget(self.message)

    # -- data --------------------------------------------------------------

    def refresh(self):
        self.rows = list(self.shipments.completed())

        years = sorted({(r["etd_belawan"] or "")[:4]
                        for r in self.rows if r["etd_belawan"]}, reverse=True)
        current = self.year_combo.currentData()
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        self.year_combo.addItem("Semua", "")
        for year in years:
            self.year_combo.addItem(year, year)
        if current:
            index = self.year_combo.findData(current)
            if index >= 0:
                self.year_combo.setCurrentIndex(index)
        self.year_combo.blockSignals(False)

        self._apply_filters()

    def _matches(self, row) -> bool:
        selected = {c.code for c in self.chips if c.isChecked()}
        if selected and row["exporter_code"] not in selected:
            return False

        year = self.year_combo.currentData()
        if year and not (row["etd_belawan"] or "").startswith(year):
            return False

        needle = self.search.text().strip().lower()
        if not needle:
            return True
        haystack = " ".join(str(row[field] or "") for field in (
            "exporter_code", "vessel_name", "voyage", "booking_number",
            "destination_port", "destination_country")).lower()
        return needle in haystack

    def _apply_filters(self):
        visible = [r for r in self.rows if self._matches(r)]

        # Keep whatever ordering the user picked; default to most recently
        # completed first.
        header = self.table.horizontalHeader()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        if not self.table.rowCount() and sort_column == 0:
            sort_column, sort_order = 6, Qt.SortOrder.DescendingOrder

        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for row in visible:
            self._add_row(row)
        self.table.setSortingEnabled(True)
        self.table.sortItems(sort_column, sort_order)

        total = len(self.rows)
        self.count_label.setText(
            f"{len(visible)} dari {total} pengiriman" if total else "")
        self.table.setVisible(bool(visible))
        self.empty_state.setVisible(not visible)
        self.empty_state.setText(
            "Belum ada pengiriman yang selesai." if not self.rows
            else "Tidak ada pengiriman yang cocok dengan filter.")

    def _add_row(self, row):
        index = self.table.rowCount()
        self.table.insertRow(index)

        # The exporter keeps its colour here too (PRD 2.4 — consistent
        # everywhere the exporter is referenced).
        code = row["exporter_code"] or ""
        exporter_item = SortableItem(code)
        exporter_item.setForeground(QColor(EXPORTER_COLORS.get(code, "#374151")))
        font = exporter_item.font()
        font.setBold(True)
        exporter_item.setFont(font)
        self.table.setItem(index, 0, exporter_item)

        sequence = row["sequence_number"] or 0
        self.table.setItem(index, 1, SortableItem(str(sequence), sequence))

        vessel = f"{row['vessel_name'] or ''} {row['voyage'] or ''}".strip()
        self.table.setItem(index, 2, SortableItem(vessel))
        self.table.setItem(index, 3, SortableItem(row["booking_number"] or ""))
        self.table.setItem(index, 4, SortableItem(row["destination_port"] or ""))

        etd = row["etd_belawan"] or ""
        self.table.setItem(index, 5,
                           SortableItem(format_date_id(etd) if etd else "-", etd))
        completed = row["completed_at"] or ""
        self.table.setItem(
            index, 6,
            SortableItem(format_date_id(completed[:10]) if completed else "-",
                         completed))

        folder = row["folder_path"] or ""
        actions = QWidget()
        actions_row = QHBoxLayout(actions)
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(6)

        open_button = QPushButton("Buka Folder")
        open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        open_button.setStyleSheet(theme.style(
            "border: 1px solid #d1d5db; border-radius: 4px; padding: 3px 10px;"
            "background: white; font-size: 11px;"))
        open_button.clicked.connect(lambda _, p=folder: self._open(p))
        actions_row.addWidget(open_button)

        delete_button = QPushButton("Hapus")
        delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_button.setStyleSheet(theme.style(
            "border: 1px solid #fca5a5; border-radius: 4px; padding: 3px 10px;"
            "background: white; color: #b91c1c; font-size: 11px;"))
        label = f"{code}{sequence}"
        delete_button.clicked.connect(
            lambda _, sid=row["id"], lbl=label, p=folder: self._delete(sid, lbl, p))
        actions_row.addWidget(delete_button)
        actions_row.addStretch(1)

        self.table.setCellWidget(index, 7, actions)

    def _delete(self, shipment_id: str, label: str, folder: str):
        detail = f"Folder:\n{folder}" if folder else "Pengiriman ini tidak punya folder."
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setWindowTitle("Hapus pengiriman?")
        confirm.setText(f"Hapus pengiriman <b>{label}</b> beserta seluruh isinya?")
        confirm.setInformativeText(
            f"{detail}\n\nFolder akan dipindahkan ke Recycle Bin (bisa "
            "dipulihkan). Catatan: folder di Google Drive terkadang langsung "
            "terhapus permanen. Tindakan ini tidak bisa dibatalkan dari dalam "
            "aplikasi.")
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        confirm.setDefaultButton(QMessageBox.StandardButton.No)
        confirm.button(QMessageBox.StandardButton.Yes).setText("Hapus")
        confirm.button(QMessageBox.StandardButton.No).setText("Batal")
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return

        # Folder-first, same rule as the detail view: keep the record if the
        # folder cannot be removed, so nothing is left half-deleted.
        result = fileops.delete_shipment_folder(folder)
        if result.existed and not result.removed:
            self.message.show_error(result.message)
            return

        self.shipments.delete(shipment_id)
        note = f"Pengiriman {label} dihapus."
        if result.existed:
            note += f" {result.message}"
        self.message.show_success(note)
        self.refresh()          # rebuild the table without the deleted row
        self.changed.emit()     # dashboard "selesai bulan ini" stat may change

    def _open(self, folder: str):
        if not folder or not Path(folder).exists():
            # PRD Section 12 note — history keeps metadata only.
            self.message.show_error(
                "Folder tidak ditemukan. Folder mungkin sudah dipindahkan atau "
                "dihapus dari Google Drive.")
            return
        self.message.clear()
        self.open_folder_requested.emit(folder)
