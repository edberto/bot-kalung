"""Shipment Detail (folder-scan tracker).

Header bar + quarantine reminder + BNCT status + the action-item list and notes.
The old A1..E6 workflow checklist (with its email/print/PDF actions) was replaced
by per-item statuses; a scanned shipment is imported already in progress, so
those construction-time actions no longer apply.
"""

from __future__ import annotations

from . import theme

import os
import subprocess
from pathlib import Path

from datetime import date as _date

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ..core.constants import EXPORTER_COLORS
from ..services import etd_change, fileops
from ..services.action_items import ActionItems
from ..services.bnct import LOGIN_URL
from ..services.containers import Containers
from ..services.naming import parse_iso_date
from ..services.shipments import Shipments
from .action_items_view import ActionItemsView, NotesPanel
from .bnct_panel import BnctPanel
from .containers_panel import ContainersPanel
from .widgets import (
    DangerButton, DateEdit, InlineMessage, Panel, SecondaryButton, days_until,
    format_date_id,
)


def _headless() -> bool:
    """Tests/CI run with the offscreen Qt platform; never spawn a real Explorer,
    browser, or Excel window there (they pile up and never close)."""
    return os.environ.get("QT_QPA_PLATFORM") == "offscreen"


def open_path_or_url(target: str) -> bool:
    """Open an external link in the default browser."""
    if _headless():
        return True
    import webbrowser

    try:
        return webbrowser.open(target)
    except webbrowser.Error:
        return False


def open_path(path: str | Path) -> bool:
    """Open a folder or file with its default Windows handler."""
    target = Path(path)
    if not target.exists():
        return False
    if _headless():
        return True
    try:
        os.startfile(str(target))  # noqa: S606 - Windows-only by design
        return True
    except (AttributeError, OSError):
        try:
            subprocess.Popen(["explorer", str(target)])
            return True
        except OSError:
            return False


class _ItemDateDialog(QDialog):
    """Pick (or clear) an action item's date."""

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tanggal Item")
        self._cleared = False
        form = QFormLayout(self)

        title = QLabel(item.title)
        title.setWordWrap(True)
        form.addRow(title)

        self.date_field = DateEdit()
        existing = parse_iso_date(item.due_date) if item.due_date else None
        self.date_field.setDate(QDate(existing.year, existing.month, existing.day)
                                if existing else QDate.currentDate())
        form.addRow("Tanggal", self.date_field)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        if item.due_date:
            clear = buttons.addButton("Hapus tanggal",
                                      QDialogButtonBox.ButtonRole.DestructiveRole)
            clear.clicked.connect(self._clear)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _clear(self):
        self._cleared = True
        self.accept()

    def value(self) -> str | None:
        if self._cleared:
            return None
        return self.date_field.date().toString("yyyy-MM-dd")


class _EtdDialog(QDialog):
    """Pick a new ETD for the shipment."""

    def __init__(self, row, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ubah ETD")
        form = QFormLayout(self)

        note = QLabel(
            "Mengubah ETD juga mengganti akhiran tanggal pada nama folder dan "
            "memperbarui nomor dokumen, tanggal VGM, dan ETD di sheet SI.")
        note.setWordWrap(True)
        note.setStyleSheet(theme.style("font-size: 11px; color: #6b7280;"))
        form.addRow(note)

        self.date_field = DateEdit()
        existing = parse_iso_date(row["etd_belawan"]) if row["etd_belawan"] else None
        self.date_field.setDate(QDate(existing.year, existing.month, existing.day)
                                if existing else QDate.currentDate())
        form.addRow("ETD baru", self.date_field)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def value(self):
        qdate = self.date_field.date()
        return _date(qdate.year(), qdate.month(), qdate.day())


class _AddItemDialog(QDialog):
    """Collects an ad-hoc action item's title."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tambah Item")
        self.setMinimumWidth(360)
        form = QFormLayout(self)

        self.title_field = QLineEdit()
        self.title_field.setPlaceholderText("Judul item baru")
        form.addRow("Judul", self.title_field)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.title_field.setFocus()

    def title(self) -> str:
        return self.title_field.text().strip()


class ShipmentDetailView(QWidget):
    """Header + quarantine banner + BNCT status + action items for one shipment."""

    error = pyqtSignal(str)
    changed = pyqtSignal()          # something changed; sidebar/dashboard reload
    completed = pyqtSignal(str)     # shipment marked complete, carries its label
    deleted = pyqtSignal(str)       # shipment removed; carries a note to show
    bnct_refresh_requested = pyqtSignal()   # kept for the window's connection
    open_vessel_monitor_requested = pyqtSignal()  # "Buka Monitor Kapal" pressed

    def __init__(self, db, settings=None):
        super().__init__()
        self.db = db
        self.settings = settings
        self.shipments = Shipments(db)
        self.items = ActionItems(db)
        self.container_store = Containers(db)
        self.shipment_id: str | None = None
        self.folder: Path | None = None
        self.workbook: Path | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 20, 28, 20)
        outer.setSpacing(12)

        # -- header bar ----------------------------------------------------
        header = Panel()
        header.setStyleSheet(theme.style(
            "background: white; border: 1px solid #e5e7eb; border-radius: 8px;"))
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.badge = QLabel()
        self.badge.setStyleSheet(theme.style("border: none;"))
        top.addWidget(self.badge)

        self.vessel_label = QLabel()
        self.vessel_label.setStyleSheet(theme.style(
            "font-size: 18px; font-weight: 700; color: #111827; border: none;"))
        top.addWidget(self.vessel_label)
        top.addStretch(1)

        self.etd_label = QLabel()
        self.etd_label.setStyleSheet(theme.style(
            "font-size: 13px; font-weight: 600; border: none;"))
        top.addWidget(self.etd_label)

        self.etd_edit_button = QPushButton("✎")
        self.etd_edit_button.setToolTip("Ubah ETD")
        self.etd_edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.etd_edit_button.setFixedWidth(24)
        self.etd_edit_button.setStyleSheet(theme.style(
            "QPushButton { border: none; background: transparent;"
            " color: #2563eb; font-size: 13px; }"
            "QPushButton:hover { color: #1d4ed8; }"))
        self.etd_edit_button.clicked.connect(self._on_edit_etd)
        top.addWidget(self.etd_edit_button)
        header_layout.addLayout(top)

        self.meta_label = QLabel()
        self.meta_label.setTextFormat(Qt.TextFormat.PlainText)
        self.meta_label.setStyleSheet(theme.style(
            "font-size: 12px; color: #6b7280; border: none;"))
        header_layout.addWidget(self.meta_label)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.folder_button = SecondaryButton("Buka Folder")
        self.folder_button.clicked.connect(self._open_folder)
        actions.addWidget(self.folder_button)

        self.excel_button = SecondaryButton("Buka Excel")
        self.excel_button.clicked.connect(self._open_excel)
        actions.addWidget(self.excel_button)

        self.complete_button = SecondaryButton("Tandai Selesai")
        self.complete_button.clicked.connect(self._mark_complete)
        actions.addWidget(self.complete_button)

        self.delete_button = DangerButton("Hapus Pengiriman")
        self.delete_button.clicked.connect(self._delete_shipment)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)

        progress_box = QVBoxLayout()
        progress_box.setSpacing(3)
        self.progress_label = QLabel()
        self.progress_label.setStyleSheet(theme.style(
            "font-size: 11px; color: #6b7280; border: none;"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setFixedWidth(180)
        self.progress_bar.setStyleSheet(theme.style("""
            QProgressBar { background: #f3f4f6; border: none; border-radius: 3px; }
            QProgressBar::chunk { background: #2563eb; border-radius: 3px; }
        """))
        progress_box.addWidget(self.progress_label)
        progress_box.addWidget(self.progress_bar)
        actions.addLayout(progress_box)
        header_layout.addLayout(actions)

        outer.addWidget(header)

        # -- quarantine reminder -------------------------------------------
        self.quarantine_banner = InlineMessage()
        outer.addWidget(self.quarantine_banner)

        self.message = InlineMessage()
        outer.addWidget(self.message)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(theme.style("color: #e5e7eb;"))
        outer.addWidget(divider)

        # -- scroll area: [items | notes] over [vessel | containers] -------
        self.items_scroll = QScrollArea()
        self.items_scroll.setWidgetResizable(True)
        self.items_scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(16)

        # Top row: action items (left) + notes (right).
        top_row = QHBoxLayout()
        top_row.setSpacing(16)
        self.items_view = ActionItemsView()
        self.items_view.status_changed.connect(self._on_status_changed)
        self.items_view.add_requested.connect(self._on_add_item)
        self.items_view.delete_requested.connect(self._on_delete_item)
        self.items_view.date_edit_requested.connect(self._on_item_date)
        top_row.addWidget(self.items_view, 3)

        self.notes_panel = NotesPanel()
        self.notes_panel.notes_edited.connect(self._on_notes_edited)
        top_row.addWidget(self.notes_panel, 2)
        scroll_layout.addLayout(top_row)

        # Bottom row: vessel tracking (left) + container tracking (right).
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(16)
        self.bnct_panel = BnctPanel(db)
        self.bnct_panel.open_monitor_requested.connect(
            self.open_vessel_monitor_requested)
        bottom_row.addWidget(self.bnct_panel, 1)

        self.containers_panel = ContainersPanel()
        self.containers_panel.open_bnct.connect(self._open_bnct_containers)
        bottom_row.addWidget(self.containers_panel, 1)
        scroll_layout.addLayout(bottom_row)
        scroll_layout.addStretch(1)

        self.items_scroll.setWidget(scroll_content)
        outer.addWidget(self.items_scroll, 1)

    # -- state -------------------------------------------------------------

    def load(self, shipment_id: str):
        self.shipment_id = shipment_id
        self.message.clear()
        row = self.shipments.get(shipment_id)
        if row is None:
            self.message.show_error("Data pengiriman tidak ditemukan.")
            return

        code = row["exporter_code"]
        self.badge.setText(f"{code}{row['sequence_number']}")
        self.badge.setStyleSheet(theme.style(
            f"background: {EXPORTER_COLORS.get(code, '#6b7280')}; color: white;"
            "border-radius: 4px; padding: 3px 10px; font-size: 12px;"
            "font-weight: 700;"))

        vessel = f"{row['vessel_name'] or ''} {row['voyage'] or ''}".strip()
        self.vessel_label.setText(vessel or "(tanpa kapal)")

        remaining = days_until(row["etd_belawan"])
        urgent = remaining is not None and remaining <= 3
        self.etd_label.setText(f"ETD {format_date_id(row['etd_belawan'])}")
        self.etd_label.setStyleSheet(theme.style(
            "font-size: 13px; font-weight: 600; border: none;"
            f"color: {'#dc2626' if urgent else '#374151'};"))

        meta = [
            f"Booking {row['booking_number'] or '-'}",
            f"{row['destination_port'] or '-'}, {row['destination_country'] or '-'}",
            f"{row['container_quantity'] or '?'} x {row['container_size_short'] or '?'}",
        ]
        if row["shipping_company"]:
            meta.append(row["shipping_company"])
        self.meta_label.setText("   ·   ".join(meta))

        self.folder = Path(row["folder_path"]) if row["folder_path"] else None
        self.workbook = self._find_workbook(self.folder)
        self.folder_button.setEnabled(self.folder is not None)
        self.excel_button.setEnabled(self.workbook is not None)

        if row["quarantine_required"]:
            self.quarantine_banner.show_warning(
                "Karantina diperlukan — pastikan Fumi/Phyto/COO diproses.")
        else:
            self.quarantine_banner.clear()

        self.bnct_panel.load(shipment_id)
        self.containers_panel.apply(self.container_store.for_shipment(shipment_id))
        self.notes_panel.set_notes(row["notes"])
        self._refresh_items()

    def refresh_bnct(self, shipment_id: str):
        if shipment_id == self.shipment_id:
            self.bnct_panel.load(shipment_id)

    def refresh_containers(self, shipment_id: str):
        """A container status changed; refresh the list if it is on screen."""
        if shipment_id == self.shipment_id:
            self.containers_panel.apply(
                self.container_store.for_shipment(shipment_id))

    def _open_bnct_containers(self):
        """Copy every container number and open the BNCT portal — the portal has
        no URL pre-fill, so the worker pastes each into the container search (the
        card shows which terminal to look at).
        """
        from PyQt6.QtWidgets import QApplication

        if self.shipment_id is None:
            return
        numbers = [c.container_no
                   for c in self.container_store.for_shipment(self.shipment_id)]
        clipboard = QApplication.clipboard()
        if clipboard is not None and numbers:
            clipboard.setText("\n".join(numbers))
        open_path_or_url(LOGIN_URL)
        if numbers:
            self.message.show_success(
                f"{len(numbers)} nomor kontainer disalin (satu per baris). "
                "Tempel di pencarian kontainer BNCT.")
        else:
            self.message.show_info("Membuka portal BNCT.")

    @staticmethod
    def _find_workbook(folder: Path | None) -> Path | None:
        if folder is None or not folder.is_dir():
            return None
        from ..services.drive import is_main_workbook

        for entry in sorted(folder.iterdir()):
            if entry.is_file() and is_main_workbook(entry.name):
                return entry
        return None

    # -- action items ------------------------------------------------------

    def _refresh_items(self):
        if self.shipment_id is None:
            return
        self.items_view.apply(self.items.list(self.shipment_id))
        done, total = self.items.progress(self.shipment_id)
        self.progress_label.setText(f"{done} dari {total} item final")
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)

    def focus_item(self, item_id: str):
        """Scroll an item into view — used when arriving from the calendar."""
        row = self.items_view.rows.get(item_id)
        if row is None:
            return
        self.items_scroll.ensureWidgetVisible(row, 50, 50)
        row.setStyleSheet(theme.style(
            "background: #eef2ff; border: 2px solid #2563eb; border-radius: 6px;"))

    def _on_status_changed(self, item_id: str, status: str):
        self.items.set_status(item_id, status)
        self._refresh_items()
        self.changed.emit()

    def _on_add_item(self):
        dialog = _AddItemDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.title():
            return
        self.items.add_custom(self.shipment_id, dialog.title())
        self._refresh_items()
        self.changed.emit()

    def _on_delete_item(self, item_id: str):
        answer = QMessageBox.question(
            self, "Hapus item?", "Hapus item tindakan ini?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.items.delete(item_id)
        self._refresh_items()
        self.changed.emit()

    def _on_item_date(self, item_id: str):
        item = next((i for i in self.items.list(self.shipment_id)
                     if i.id == item_id), None)
        if item is None:
            return
        dialog = _ItemDateDialog(item, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.items.set_due_date(item_id, dialog.value())
        self._refresh_items()
        self.changed.emit()

    def _on_notes_edited(self, text: str):
        if self.shipment_id is None:
            return
        self.shipments.set_notes(self.shipment_id, text)
        self.message.show_success("Catatan disimpan.")
        self.changed.emit()

    # -- ETD ---------------------------------------------------------------

    def _on_edit_etd(self):
        """Change the ETD, updating the folder's date suffix and the Excel
        cells that derive from it. Check-then-run, like the resequence flow.
        """
        row = self.shipments.get(self.shipment_id)
        if row is None:
            return
        dialog = _EtdDialog(row, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_etd = dialog.value()
        if new_etd is None or new_etd.isoformat() == (row["etd_belawan"] or ""):
            return

        plan = etd_change.build_plan(row, new_etd)
        blockers = etd_change.preflight(plan)
        if blockers:
            QMessageBox.warning(
                self, "Tutup berkas dulu",
                "Tutup berkas berikut lalu coba lagi:\n\n"
                + "\n".join(f"  • {b.path} — {b.reason}" for b in blockers))
            return

        summary = [f"ETD {format_date_id(row['etd_belawan'])} → "
                   f"{format_date_id(new_etd.isoformat())}"]
        if plan.new_folder is not None:
            summary.append(f"Folder → {plan.new_folder.name}")
        if plan.document_number_changes:
            summary.append(f"Nomor dokumen {plan.old_document_number} → "
                           f"{plan.new_document_number}")
        summary.append(f"VGM DATE → {plan.new_vgm_date}")
        summary.append(f"ETD di SI → {plan.new_si_etd}")
        answer = QMessageBox.question(
            self, "Ubah ETD?", "\n".join(summary) + "\n\nLanjutkan?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return

        result = etd_change.execute(self.db, plan)
        if result.ok:
            self.message.show_success("; ".join(result.changed))
        else:
            self.message.show_error("; ".join(result.failed))
        self.load(self.shipment_id)
        self.changed.emit()

    # -- completion / deletion --------------------------------------------

    def _mark_complete(self):
        row = self.shipments.get(self.shipment_id)
        if row is None:
            return
        label = f"{row['exporter_code']}{row['sequence_number']}"
        answer = QMessageBox.question(
            self, "Tandai pengiriman selesai?",
            f"Pengiriman {label} akan dipindahkan ke Riwayat.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.shipments.mark_complete(self.shipment_id)
        self.completed.emit(label)

    def _open_folder(self):
        if self.folder is None or not open_path(self.folder):
            self.message.show_error(
                "Folder pengiriman tidak dapat dibuka. Folder mungkin sudah "
                "dipindahkan atau dihapus dari Google Drive.")
        else:
            self.message.clear()

    def _open_excel(self):
        if self.workbook is None or not open_path(self.workbook):
            self.message.show_error(
                "File Excel tidak dapat dibuka. Periksa folder pengiriman.")
        else:
            self.message.clear()

    def _delete_shipment(self):
        """Remove the shipment and send its folder to the Recycle Bin."""
        row = self.shipments.get(self.shipment_id)
        if row is None:
            return
        label = f"{row['exporter_code']}{row['sequence_number']}"
        folder = row["folder_path"]

        detail = (f"Folder:\n{folder}" if folder
                  else "Pengiriman ini tidak punya folder.")
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setWindowTitle("Hapus pengiriman?")
        confirm.setText(
            f"Hapus pengiriman <b>{label}</b> beserta seluruh isinya?")
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

        # Folder first: if it cannot be removed, keep the record so nothing is
        # half-deleted. A missing or absent folder is not a failure.
        result = fileops.delete_shipment_folder(folder)
        if result.existed and not result.removed:
            self.message.show_error(result.message)
            return

        self.shipments.delete(self.shipment_id)

        note = f"Pengiriman {label} dihapus."
        if result.existed:
            note += f" {result.message}"
        self.shipment_id = None
        self.deleted.emit(note)

    def shutdown(self):
        """No background workers remain; kept for the window's teardown hook."""
        return
