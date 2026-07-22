"""Change a shipment's sequence number (2026-07-21).

The flow is deliberately check-then-run, because the migration renames folders
and rewrites cells inside Excel:

1. pick the shipment and its new number,
2. resolve every clash — the worker decides where each displaced shipment goes,
3. pre-flight for open files, so nothing is half-renamed; the worker closes them
   and re-checks,
4. review the exact plan, then run it.
"""

from __future__ import annotations

from . import theme

from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QInputDialog, QLabel, QPlainTextEdit, QVBoxLayout,
)

from ..services import resequence as rs
from .widgets import ComboBox, InlineMessage, PrimaryButton, SecondaryButton, SpinBox


class ResequenceDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.moves: dict[str, int] = {}
        self.plan: list[rs.Move] = []

        self.setWindowTitle("Ubah Nomor Urut")
        self.setMinimumSize(620, 520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)

        intro = QLabel(
            "Mengubah nomor urut akan mengganti nama folder, nama workbook dan "
            "invoice, nomor dokumen di dalam Excel, serta nama PDF yang sudah "
            "diekspor.")
        intro.setWordWrap(True)
        intro.setStyleSheet(theme.style("font-size: 12px; color: #6b7280;"))
        outer.addWidget(intro)

        picker = QHBoxLayout()
        picker.setSpacing(8)
        self.shipment_combo = ComboBox()
        for row in self._shipments():
            vessel = f"{row['vessel_name'] or ''} {row['voyage'] or ''}".strip()
            self.shipment_combo.addItem(
                f"{rs.label_of(row)}  —  {vessel or '(tanpa kapal)'}", row["id"])
        picker.addWidget(self.shipment_combo, 1)

        picker.addWidget(QLabel("Nomor baru"))
        self.sequence_field = SpinBox()
        self.sequence_field.setRange(1, 999)
        picker.addWidget(self.sequence_field)

        self.check_button = SecondaryButton("Periksa")
        self.check_button.clicked.connect(self._check)
        picker.addWidget(self.check_button)
        outer.addLayout(picker)

        self.message = InlineMessage()
        outer.addWidget(self.message)

        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setPlaceholderText(
            "Pilih pengiriman dan nomor baru, lalu tekan Periksa.")
        self.summary.setStyleSheet(theme.style(
            "border: 1px solid #e5e7eb; border-radius: 6px; font-size: 12px;"))
        outer.addWidget(self.summary, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.run_button = PrimaryButton("Jalankan Migrasi")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self._run)
        buttons.addWidget(self.run_button)
        close = SecondaryButton("Tutup")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        outer.addLayout(buttons)

        self._sync_default()
        self.shipment_combo.currentIndexChanged.connect(self._sync_default)

    # -- data -------------------------------------------------------------

    def _shipments(self):
        return self.db.query(
            "SELECT * FROM shipments ORDER BY exporter_code, sequence_number")

    def _sync_default(self):
        """Default the spinbox to the selected shipment's current number."""
        shipment_id = self.shipment_combo.currentData()
        row = self.db.query_one(
            "SELECT sequence_number FROM shipments WHERE id=?", (shipment_id,))
        if row is not None:
            self.sequence_field.setValue(row["sequence_number"])
        self.run_button.setEnabled(False)
        self.summary.clear()

    # -- check -------------------------------------------------------------

    def _check(self):
        shipment_id = self.shipment_combo.currentData()
        new_sequence = self.sequence_field.value()
        if not shipment_id:
            return
        rows = self._shipments()
        current = next((r for r in rows if r["id"] == shipment_id), None)
        if current is None:
            return
        if current["sequence_number"] == new_sequence:
            self.message.show_warning("Nomor barunya sama dengan nomor sekarang.")
            self.run_button.setEnabled(False)
            return

        moves = {shipment_id: new_sequence}
        # Let the worker decide where each displaced shipment goes, until the
        # whole assignment is free of clashes.
        while True:
            clash = rs.find_clash(rows, moves)
            if clash is None:
                break
            value, ok = QInputDialog.getInt(
                self, "Nomor bentrok",
                f"Nomor {clash.sequence} sudah dipakai oleh {clash.label}.\n"
                f"Pindahkan {clash.label} ke nomor berapa?",
                clash.sequence + 1, 1, 999)
            if not ok:
                self.message.show_warning("Migrasi dibatalkan.")
                self.run_button.setEnabled(False)
                return
            moves[clash.shipment_id] = value

        self.moves = moves
        self.plan = rs.build_plan(rows, moves)
        blockers = rs.preflight(self.plan)

        if blockers:
            lines = ["Tutup dulu berkas berikut, lalu tekan Periksa lagi:", ""]
            lines += [f"  • [{b.label}] {b.path} — {b.reason}" for b in blockers]
            self.summary.setPlainText("\n".join(lines))
            self.message.show_error(
                f"{len(blockers)} berkas masih terbuka. Tutup lalu periksa ulang.")
            self.run_button.setEnabled(False)
            return

        self.summary.setPlainText(self._describe(self.plan))
        self.message.show_success(
            f"{len(self.plan)} pengiriman siap dimigrasi. Periksa lalu jalankan.")
        self.run_button.setEnabled(True)

    @staticmethod
    def _describe(plan: list[rs.Move]) -> str:
        lines: list[str] = []
        for move in plan:
            lines.append(f"{move.label}  →  {move.new_label}")
            if move.new_folder is not None:
                lines.append(f"    Folder   : {move.folder.name}")
                lines.append(f"             → {move.new_folder.name}")
            for rename in move.file_renames:
                lines.append(f"    Berkas   : {rename.source.name}")
                lines.append(f"             → {rename.target.name}")
            if move.new_document_number:
                lines.append(f"    Nomor dok: {move.old_document_number}"
                             f" → {move.new_document_number}")
            if move.pdf_renames:
                lines.append(f"    PDF      : {len(move.pdf_renames)} berkas "
                             "diganti namanya")
            for note in move.notes:
                lines.append(f"    ! {note}")
            lines.append("")
        return "\n".join(lines)

    # -- run ---------------------------------------------------------------

    def _run(self):
        if not self.plan:
            return
        self.run_button.setEnabled(False)
        result = rs.execute(self.db, self.plan)

        lines = []
        if result.changed:
            lines += ["Berhasil:"] + [f"  ✓ {c}" for c in result.changed] + [""]
        if result.warnings:
            lines += ["Catatan:"] + [f"  ! {w}" for w in result.warnings] + [""]
        if result.failed:
            lines += ["Gagal:"] + [f"  ✗ {f}" for f in result.failed]
        self.summary.setPlainText("\n".join(lines))

        if result.ok:
            self.message.show_success("Migrasi selesai.")
        else:
            self.message.show_error(
                "Sebagian langkah gagal — periksa rinciannya di bawah.")
        self.plan = []
        self._reload_picker()

    def _reload_picker(self):
        self.shipment_combo.blockSignals(True)
        self.shipment_combo.clear()
        for row in self._shipments():
            vessel = f"{row['vessel_name'] or ''} {row['voyage'] or ''}".strip()
            self.shipment_combo.addItem(
                f"{rs.label_of(row)}  —  {vessel or '(tanpa kapal)'}", row["id"])
        self.shipment_combo.blockSignals(False)
