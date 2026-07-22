"""Shipment Detail (PRD Section 6).

This phase delivers the header bar (6.1) and the quarantine reminder (6.1.1).
The workflow checklist (6.2/6.3) follows; its area says so rather than looking
like an empty list.
"""

from __future__ import annotations

from . import theme

import os
import subprocess
from pathlib import Path

from datetime import date as _date

from PyQt6.QtCore import QDate, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ..core.constants import (
    EXPORTER_COLORS, NON_COMPLETING_ACTION_KINDS, STEPS_COMPLETED_BY_ACTION,
    STEPS_WITH_SIDE_EFFECTS,
)
from ..services import (
    etd_change, fileops, messaging, pdf_export, printing, whatsapp,
)
from ..services.messaging import DraftBuilder, MessagingError
from ..services.naming import parse_iso_date
from ..services.shipments import Shipments
from .bnct_panel import BnctPanel
from .checklist import ChecklistView
from .widgets import (
    DangerButton, DateEdit, InlineMessage, Panel, SecondaryButton, days_until,
    format_date_id,
)


class _PdfExportWorker(QThread):
    """E4 drives Excel, which takes seconds and must not freeze the window."""

    done = pyqtSignal(object)

    def __init__(self, workbook, folder, exporter_code, sequence, parent=None):
        super().__init__(parent)
        self._args = (workbook, folder, exporter_code, sequence)

    def run(self):
        workbook, folder, exporter_code, sequence = self._args
        # export_shipment never raises, so `done` always carries a result.
        self.done.emit(pdf_export.export_shipment(
            workbook, exporter_code=exporter_code, sequence=sequence,
            folder=folder))


def open_path_or_url(target: str) -> bool:
    """Open an external link in the default browser."""
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
    try:
        os.startfile(str(target))  # noqa: S606 - Windows-only by design
        return True
    except (AttributeError, OSError):
        try:
            subprocess.Popen(["explorer", str(target)])
            return True
        except OSError:
            return False


class _StepDateDialog(QDialog):
    """Pick (or clear) a step's date."""

    def __init__(self, step, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tanggal Langkah")
        self._cleared = False
        form = QFormLayout(self)

        title = QLabel(f"{step.display_number}. {step.title}")
        title.setWordWrap(True)
        form.addRow(title)

        self.date_field = DateEdit()
        existing = parse_iso_date(step.due_date) if step.due_date else None
        self.date_field.setDate(QDate(existing.year, existing.month, existing.day)
                                if existing else QDate.currentDate())
        form.addRow("Tanggal", self.date_field)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        if step.due_date:
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


class _AddStepDialog(QDialog):
    """Collects a custom step's title and where to insert it."""

    def __init__(self, steps, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tambah Langkah")
        self.setMinimumWidth(360)
        form = QFormLayout(self)

        self.title_field = QLineEdit()
        self.title_field.setPlaceholderText("Judul langkah baru")
        form.addRow("Judul", self.title_field)

        self.position_combo = QComboBox()
        self.position_combo.addItem("Di akhir daftar", (None, "after"))
        if steps:
            self.position_combo.addItem("Di awal daftar", (steps[0].code, "before"))
        for step in steps:
            self.position_combo.addItem(
                f"Setelah {step.display_number}. {step.title}",
                (step.code, "after"))
        form.addRow("Posisi", self.position_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.title_field.setFocus()

    def result_values(self) -> tuple[str, str | None, str]:
        anchor, side = self.position_combo.currentData()
        return self.title_field.text().strip(), anchor, side


class ShipmentDetailView(QWidget):
    """Header + quarantine banner for one shipment."""

    error = pyqtSignal(str)

    changed = pyqtSignal()          # a step changed; sidebar/dashboard reload
    completed = pyqtSignal(str)     # shipment marked complete, carries its label
    deleted = pyqtSignal(str)       # shipment removed; carries a note to show
    bnct_refresh_requested = pyqtSignal()   # "Periksa Sekarang" pressed

    def __init__(self, db, settings=None):
        super().__init__()
        self.db = db
        self.settings = settings
        self.shipments = Shipments(db)
        self.drafts = DraftBuilder(db, settings) if settings is not None else None
        self.shipment_id: str | None = None
        self.folder: Path | None = None
        self.workbook: Path | None = None
        self.pdf_worker: _PdfExportWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 20, 28, 20)
        outer.setSpacing(12)

        # -- header bar (PRD 6.1) -----------------------------------------
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

        # The ETD is editable: changing it also renames the folder's date
        # suffix and rewrites the VGM/SI cells that derive from it.
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

        # -- quarantine reminder (PRD 6.1.1) -------------------------------
        self.quarantine_banner = InlineMessage()
        outer.addWidget(self.quarantine_banner)

        # -- BNCT monitoring status (PRD 15) -------------------------------
        self.bnct_panel = BnctPanel(db, on_refresh=self.bnct_refresh_requested.emit)
        outer.addWidget(self.bnct_panel)

        self.message = InlineMessage()
        outer.addWidget(self.message)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(theme.style("color: #e5e7eb;"))
        outer.addWidget(divider)

        # Kept as an attribute so a step can be scrolled into view (focus_step).
        self.checklist_scroll = QScrollArea()
        self.checklist_scroll.setWidgetResizable(True)
        self.checklist_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.checklist = ChecklistView()
        self.checklist.step_toggled.connect(self._on_step_toggled)
        self.checklist.action_requested.connect(self._on_action)
        self.checklist.mark_complete_requested.connect(self._on_mark_complete)
        self.checklist.remark_edited.connect(self._on_remark_edited)
        self.checklist.add_requested.connect(self._on_add_custom)
        self.checklist.delete_requested.connect(self._on_delete_custom)
        self.checklist.move_requested.connect(self._on_move_custom)
        self.checklist.date_edit_requested.connect(self._on_date_edit)
        self.checklist_scroll.setWidget(self.checklist)
        outer.addWidget(self.checklist_scroll, 1)

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

        # PRD 6.1.1 — visible until B3 (Pay LOLO empty) is complete.
        b3_done = any(s.code == "B3" and s.is_complete
                      for s in self.shipments.steps(shipment_id))
        if row["quarantine_required"] and not b3_done:
            self.quarantine_banner.show_warning(
                "Karantina diperlukan — tulis tanggal dan lokasi pemeriksaan "
                "di SI yang sudah dicetak.")
        else:
            self.quarantine_banner.clear()

        self.bnct_panel.load(shipment_id)
        self._refresh_checklist()

    def refresh_bnct(self, shipment_id: str):
        """The controller recorded a new check; update the panel if it is the
        shipment currently on screen.
        """
        if shipment_id == self.shipment_id:
            self.bnct_panel.load(shipment_id)

    @staticmethod
    def _find_workbook(folder: Path | None) -> Path | None:
        if folder is None or not folder.is_dir():
            return None
        from ..services.drive import MAIN_WORKBOOK_RE

        for entry in sorted(folder.iterdir()):
            if entry.is_file() and MAIN_WORKBOOK_RE.match(entry.name):
                return entry
        return None

    # -- checklist ---------------------------------------------------------

    def _refresh_checklist(self):
        if self.shipment_id is None:
            return
        row = self.shipments.get(self.shipment_id)
        etd_passed = False
        if row is not None:
            remaining = days_until(row["etd_belawan"])
            etd_passed = remaining is not None and remaining < 0
        self.checklist.apply(self.shipments.steps(self.shipment_id),
                             overdue=etd_passed)

        done, total = self.shipments.progress(self.shipment_id)
        self.progress_label.setText(f"{done} dari {total} langkah selesai")
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)

    def _on_step_toggled(self, code: str, complete: bool):
        """PRD 6.2 — unchecking is always silent; re-checking a step with a
        side effect confirms first.
        """
        if complete and code in STEPS_WITH_SIDE_EFFECTS:
            title, message = STEPS_WITH_SIDE_EFFECTS[code]
            answer = QMessageBox.question(
                self, title, message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self._refresh_checklist()  # revert the checkbox
                return
            if code == "B2":
                self._reprint_si()
                return

        self.shipments.set_step(self.shipment_id, code, complete)
        self.load(self.shipment_id)   # reloads the checklist and the header
        self.changed.emit()

    # -- dates -------------------------------------------------------------

    def focus_step(self, code: str):
        """Scroll a step into view and highlight it — used when arriving from
        the calendar. The highlight clears on the next load().
        """
        row = self.checklist.rows.get(code)
        if row is None:
            return
        self.checklist_scroll.ensureWidgetVisible(row, 50, 50)
        row.setStyleSheet(theme.style(
            "background: #eef2ff; border: 2px solid #2563eb;"
            "border-radius: 6px;"))

    def _on_date_edit(self, code: str):
        step = next((s for s in self.shipments.steps(self.shipment_id)
                     if s.code == code), None)
        if step is None:
            return
        dialog = _StepDateDialog(step, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.shipments.set_step_date(self.shipment_id, code, dialog.value())
        self.load(self.shipment_id)
        self.changed.emit()

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

    # -- custom steps and remarks ------------------------------------------

    def _current_author(self) -> str | None:
        return self.settings.get("my_email") if self.settings else None

    def _on_remark_edited(self, code: str, text: str):
        self.shipments.set_step_remark(
            self.shipment_id, code, text, author=self._current_author())
        self.load(self.shipment_id)
        self.changed.emit()

    def _on_add_custom(self):
        steps = self.shipments.steps(self.shipment_id)
        dialog = _AddStepDialog(steps, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        title, anchor, side = dialog.result_values()
        if not title:
            return
        self.shipments.add_custom_step(
            self.shipment_id, title, author=self._current_author(),
            anchor_code=anchor, side=side)
        self.load(self.shipment_id)
        self.changed.emit()

    def _on_delete_custom(self, code: str):
        answer = QMessageBox.question(
            self, "Hapus langkah?",
            "Hapus langkah tambahan ini beserta catatannya?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.shipments.delete_step(self.shipment_id, code)
        self.load(self.shipment_id)
        self.changed.emit()

    def _on_move_custom(self, code: str, direction: str):
        ordered = self.shipments.steps(self.shipment_id)
        index = next((i for i, s in enumerate(ordered) if s.code == code), None)
        if index is None:
            return
        if direction == "up" and index > 0:
            anchor, side = ordered[index - 1].code, "before"
        elif direction == "down" and index < len(ordered) - 1:
            anchor, side = ordered[index + 1].code, "after"
        else:
            return
        self.shipments.move_step(
            self.shipment_id, code, anchor_code=anchor, side=side)
        self.load(self.shipment_id)
        self.changed.emit()

    def _on_action(self, code: str, kind: str, payload: str):
        if kind == "url":
            open_path_or_url(payload)
        elif kind == "whatsapp":
            ok, note = whatsapp.open_whatsapp()
            (self.message.show_success if ok else self.message.show_error)(note)
        elif kind == "excel":
            self._open_excel()
        elif kind == "reprint":
            self._reprint_si()
            return
        elif kind == "pdf":
            self._export_pdf()
            return
        elif kind == "template":
            if not self._open_template(payload):
                return

        # PRD 6.3 — only the email buttons stand in for the work being done.
        if kind in NON_COMPLETING_ACTION_KINDS:
            return
        if code in STEPS_COMPLETED_BY_ACTION:
            self.shipments.set_step(self.shipment_id, code, True, source="auto")
            self._refresh_checklist()
            self.load(self.shipment_id)
            self.changed.emit()

    def _open_template(self, template_id: str) -> bool:
        if self.drafts is None:
            self.message.show_error("Pengaturan tidak tersedia.")
            return False
        row = self.shipments.get(self.shipment_id)
        if row is None:
            return False
        try:
            draft = self.drafts.build(template_id, row)
        except MessagingError as exc:
            self.message.show_error(str(exc))
            return False

        if not messaging.open_draft(draft):
            self.message.show_error("Tidak dapat membuka browser.")
            return False
        self.message.show_success(
            "Draf email dibuka. Tambahkan penerima luar di Gmail.")
        return True

    def _reprint_si(self):
        if self.workbook is None:
            self.message.show_error("File Excel tidak ditemukan untuk dicetak.")
            self._refresh_checklist()
            return
        result = printing.print_si(self.workbook)
        if result.printed:
            self.message.show_success(result.message)
            self.shipments.set_step(self.shipment_id, "B2", True, source="auto")
        else:
            # PRD Section 5 — a failed print leaves B2 pending for a retry.
            self.message.show_warning(result.message)
            self.shipments.set_step(self.shipment_id, "B2", False)
        self.load(self.shipment_id)   # reloads the checklist and the header
        self.changed.emit()

    def _on_mark_complete(self):
        """PRD 6.4."""
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

    # -- actions -----------------------------------------------------------

    def _open_folder(self):
        if self.folder is None or not open_path(self.folder):
            # PRD Section 12 note — the folder may have moved or been deleted.
            self.message.show_error(
                "Folder pengiriman tidak dapat dibuka. Folder mungkin sudah "
                "dipindahkan atau dihapus dari Google Drive.")
        else:
            self.message.clear()

    def _export_pdf(self):
        """PRD 6.3 step E4 — write the document sheets into the PDF subfolder."""
        if self.workbook is None or self.folder is None:
            self.message.show_error(
                "File Excel tidak ditemukan untuk diekspor.")
            return
        row = self.shipments.get(self.shipment_id)
        if row is None:
            return
        if self.pdf_worker is not None and self.pdf_worker.isRunning():
            return   # already exporting; a second click would fight for Excel

        self.message.show_info("Mengekspor ke PDF...")
        self.pdf_worker = _PdfExportWorker(
            self.workbook, self.folder, row["exporter_code"],
            row["sequence_number"], self)
        self.pdf_worker.done.connect(self._on_pdf_exported)
        self.pdf_worker.start()

    def _on_pdf_exported(self, result):
        # E4 is only ticked when files were actually written, so a failed
        # export leaves the step pending for a retry (as B2's print does).
        if result.ok:
            self.message.show_success(result.message())
            self.shipments.set_step(self.shipment_id, "E4", True, source="auto")
        else:
            self.message.show_warning(result.message())
        self.load(self.shipment_id)
        self.changed.emit()

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
        """Let a running export finish before the window goes away."""
        if self.pdf_worker is not None and self.pdf_worker.isRunning():
            self.pdf_worker.wait(15000)

    def _open_excel(self):
        if self.workbook is None or not open_path(self.workbook):
            self.message.show_error(
                "File Excel tidak dapat dibuka. Periksa folder pengiriman.")
        else:
            self.message.clear()
