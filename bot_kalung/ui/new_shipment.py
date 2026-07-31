"""New Shipment wizard (PRD Section 5). Three steps, Indonesian throughout."""

from __future__ import annotations

from . import theme

from datetime import date
from pathlib import Path

from PyQt6.QtCore import QDate, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..core.constants import EXPORTER_COLORS, EXPORTERS
from ..core.context import AppContext
from ..services import do_parser, drive, excel, naming, printing
from ..services.do_parser import ExtractedDO
from ..services.fileops import FileOpError, create_shipment_folder, find_permit
from ..services.llm import LLMClient, LLMError
from ..services.shipments import Shipments
from .widgets import (
    DateEdit, InlineMessage, Panel, PrimaryButton, SecondaryButton,
    SpinBox, format_date_id,
)

DATE_FORMAT = "yyyy-MM-dd"


class _ExtractionWorker(QThread):
    """Runs pdfplumber + the LLM off the UI thread (PRD 5, Step 1)."""

    done = pyqtSignal(object, str)  # ExtractedDO | None, error message

    def __init__(self, client: LLMClient, pdf_path: Path):
        super().__init__()
        self.client = client
        self.pdf_path = pdf_path

    def run(self):
        try:
            data = do_parser.extract(self.client, self.pdf_path)
        except LLMError as exc:
            self.done.emit(None, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.done.emit(None, f"Tidak dapat membaca file DO: {exc}")
        else:
            self.done.emit(data, "")


class _PermitWorker(QThread):
    """Background import-permit check (PRD Section 5). Non-blocking."""

    done = pyqtSignal(str, str)  # level, message

    def __init__(self, client: LLMClient, source_folder: Path | None):
        super().__init__()
        self.client = client
        self.source_folder = source_folder

    def run(self):
        if self.source_folder is None or not self.source_folder.is_dir():
            self.done.emit("warning", "Tidak dapat memeriksa izin impor: "
                                      "folder sumber tidak ditemukan.")
            return
        permit = find_permit(self.source_folder)
        if permit is None:
            self.done.emit("warning",
                           "Tidak ditemukan file izin impor (PERMIT-*.pdf) di "
                           "folder pengiriman sebelumnya. Pastikan izin "
                           "tersedia sebelum pengiriman.")
            return
        try:
            text = do_parser.extract_text(permit)
            expiry_text = self.client.extract_permit_expiry(text)
        except Exception as exc:  # noqa: BLE001
            self.done.emit("warning",
                           f"Tidak dapat membaca tanggal kedaluwarsa dari "
                           f"{permit.name}. Periksa dokumen secara manual. ({exc})")
            return

        expiry = naming.parse_iso_date(expiry_text)
        if expiry is None:
            self.done.emit("warning",
                           f"Tidak dapat membaca tanggal kedaluwarsa dari "
                           f"{permit.name}. Periksa dokumen secara manual.")
            return

        remaining = (expiry - date.today()).days
        shown = expiry.isoformat()
        if remaining < 0:
            self.done.emit("error", f"Izin impor sudah kedaluwarsa pada {shown}. "
                                    "Perbarui dokumen PERMIT sebelum melanjutkan.")
        elif remaining <= 30:
            self.done.emit("warning", f"Izin impor akan kedaluwarsa pada {shown} "
                                      "— segera perbarui.")
        else:
            self.done.emit("success", f"Izin impor berlaku hingga {shown}.")


class _CreateWorker(QThread):
    """Folder operations + Excel pre-fill (PRD Sections 9 and 10)."""

    progress = pyqtSignal(str)
    warnings = pyqtSignal(list)     # sheets the pre-fill could not adjust
    done = pyqtSignal(object, str)  # result | None, error

    def __init__(self, *, source_folder, target_parent, folder_name, do_pdf,
                 sequence, etd, booking, vessel, voyage, quantity, size_short):
        super().__init__()
        self.args = dict(
            source_folder=source_folder, target_parent=target_parent,
            folder_name=folder_name, do_pdf=do_pdf, sequence=sequence, etd=etd,
            booking=booking, vessel=vessel, voyage=voyage, quantity=quantity,
            size_short=size_short)

    def run(self):
        a = self.args
        try:
            result = create_shipment_folder(
                source_folder=a["source_folder"], target_parent=a["target_parent"],
                final_folder_name=a["folder_name"], do_pdf_path=a["do_pdf"],
                new_sequence=a["sequence"], progress=self.progress.emit)
        except FileOpError as exc:
            self.done.emit(None, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.done.emit(None, f"Gagal menyiapkan folder: {exc}")
            return

        if result.main_workbook is not None:
            self.progress.emit("Mengisi Excel...")
            try:
                import xlwings as xw

                # Left visible and open for review, per PRD Section 10.
                app = xw.App(visible=True, add_book=False)
                book = app.books.open(str(result.main_workbook))
                report = excel.prefill_workbook(
                    book, sequence=a["sequence"], etd=a["etd"],
                    booking_number=a["booking"], vessel_name=a["vessel"],
                    voyage=a["voyage"], container_quantity=a["quantity"],
                    size_short=a["size_short"])
                book.save()
            except Exception as exc:  # noqa: BLE001
                self.done.emit(result, f"Folder dibuat, tetapi pengisian Excel "
                                       f"gagal: {exc}")
                return

            # Sheets the pre-fill could not adjust (NIT's SI and P.List describe
            # cargo in prose) must reach the worker, or they will not know to
            # correct the container count by hand.
            self.warnings.emit(report.warnings)

        self.done.emit(result, "")


class ExporterCard(Panel):
    """One selectable exporter tile (PRD Section 5, Step 1)."""

    clicked = pyqtSignal(str)

    def __init__(self, code: str):
        super().__init__()
        self.code = code
        self.selected = False
        self.setFixedHeight(58)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        label = QLabel(code)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(theme.style(
            f"color: {EXPORTER_COLORS.get(code, '#374151')}; font-weight: 700;"
            "font-size: 13px; border: none; background: transparent;"))
        layout.addWidget(label)
        self._restyle()

    def _restyle(self):
        color = EXPORTER_COLORS.get(self.code, "#6b7280")
        border = color if self.selected else "#e5e7eb"
        width = 2 if self.selected else 1
        background = "#f5f7ff" if self.selected else "white"
        self.setStyleSheet(theme.style(
            f"background: {background}; border: {width}px solid {border};"
            "border-radius: 8px;"))

    def set_selected(self, value: bool):
        self.selected = value
        self._restyle()

    def mousePressEvent(self, event):
        self.clicked.emit(self.code)
        super().mousePressEvent(event)


class DropZone(Panel):
    """Drag-and-drop / click target for the DO PDF."""

    file_chosen = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(110)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_border("#d1d5db")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        self.label = QLabel("Tarik file DO (PDF) ke sini, atau klik untuk memilih")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet(theme.style(
            "color: #6b7280; font-size: 13px; border: none; background: transparent;"))
        layout.addWidget(self.label)

    def _set_border(self, color: str):
        self.setStyleSheet(theme.style(
            f"background: #fafafa; border: 2px dashed {color}; border-radius: 8px;"))

    def set_filename(self, name: str | None):
        if name:
            self.label.setText(f"{name}")
            self._set_border("#2563eb")
        else:
            self.label.setText(
                "Tarik file DO (PDF) ke sini, atau klik untuk memilih")
            self._set_border("#d1d5db")

    def mousePressEvent(self, event):
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Pilih file DO", "", "PDF (*.pdf)")
        if chosen:
            self.file_chosen.emit(chosen)
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if self._pdf_from(event) is not None:
            self._set_border("#2563eb")
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._set_border("#d1d5db")

    def dropEvent(self, event):
        path = self._pdf_from(event)
        if path:
            self.file_chosen.emit(path)
            event.acceptProposedAction()

    @staticmethod
    def _pdf_from(event) -> str | None:
        data = event.mimeData()
        if not data.hasUrls():
            return None
        for url in data.urls():
            local = url.toLocalFile()
            if local.lower().endswith(".pdf"):
                return local
        return None


class NewShipmentWizard(QWidget):
    cancelled = pyqtSignal()
    created = pyqtSignal(str)          # shipment id
    open_folder_requested = pyqtSignal(str)

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.shipments = Shipments(ctx.db)

        self.exporter: str | None = None
        self.pdf_path: Path | None = None
        self.extracted = ExtractedDO()
        self.source_folder: Path | None = None
        self.sequence_width = 1
        self.created_folder: Path | None = None
        self.workers: list[QThread] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(14)

        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet(theme.style("font-size: 22px; font-weight: 700; color: #111827;"))
        header.addWidget(self.title)
        header.addStretch(1)
        self.step_label = QLabel()
        self.step_label.setStyleSheet(theme.style("font-size: 12px; color: #6b7280;"))
        header.addWidget(self.step_label)
        cancel = SecondaryButton("Batal")
        cancel.clicked.connect(self._cancel)
        header.addWidget(cancel)
        outer.addLayout(header)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(theme.style("color: #e5e7eb;"))
        outer.addWidget(divider)

        from PyQt6.QtWidgets import QStackedWidget

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_step1())
        self.stack.addWidget(self._build_step2())
        self.stack.addWidget(self._build_step3())
        outer.addWidget(self.stack, 1)

        nav = QHBoxLayout()
        self.back_button = SecondaryButton("Kembali")
        self.back_button.clicked.connect(self._go_back)
        nav.addWidget(self.back_button)
        nav.addStretch(1)
        self.next_button = PrimaryButton("Lanjut")
        self.next_button.clicked.connect(self._go_next)
        nav.addWidget(self.next_button)
        outer.addLayout(nav)

        self._refresh()

    # -- Step 1 -----------------------------------------------------------

    def _build_step1(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        layout.addWidget(self._label("Pilih eksportir"))
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.exporter_cards: dict[str, ExporterCard] = {}
        for code in EXPORTERS:
            card = ExporterCard(code)
            card.clicked.connect(self._choose_exporter)
            self.exporter_cards[code] = card
            cards.addWidget(card)
        layout.addLayout(cards)

        layout.addWidget(self._label("Unggah file DO"))
        self.drop_zone = DropZone()
        self.drop_zone.file_chosen.connect(self._choose_pdf)
        layout.addWidget(self.drop_zone)

        remove_row = QHBoxLayout()
        remove_row.addStretch(1)
        self.remove_button = QPushButton("× Hapus file")
        self.remove_button.setFlat(True)
        self.remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_button.setStyleSheet(theme.style(
            "border: none; color: #6b7280; text-decoration: underline;"))
        self.remove_button.clicked.connect(lambda: self._choose_pdf(None))
        self.remove_button.setVisible(False)
        remove_row.addWidget(self.remove_button)
        layout.addLayout(remove_row)

        self.step1_message = InlineMessage()
        layout.addWidget(self.step1_message)
        layout.addStretch(1)
        return page

    def _choose_exporter(self, code: str):
        self.exporter = code
        for card_code, card in self.exporter_cards.items():
            card.set_selected(card_code == code)
        self._refresh()

    def _choose_pdf(self, path: str | None):
        self.pdf_path = Path(path) if path else None
        self.drop_zone.set_filename(self.pdf_path.name if self.pdf_path else None)
        self.remove_button.setVisible(self.pdf_path is not None)
        self._refresh()

    # -- Step 2 -----------------------------------------------------------

    def _build_step2(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setSpacing(10)

        self.extract_message = InlineMessage()
        outer.addWidget(self.extract_message)
        self.carrier_message = InlineMessage()
        outer.addWidget(self.carrier_message)
        self.quarantine_message = InlineMessage()
        outer.addWidget(self.quarantine_message)
        self.permit_message = InlineMessage()
        outer.addWidget(self.permit_message)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        form = QFormLayout(host)
        form.setSpacing(8)

        self.booking_field = QLineEdit()
        self.vessel_field = QLineEdit()
        self.voyage_field = QLineEdit()

        self.etd_field = DateEdit()
        self.etd_field.setDisplayFormat(DATE_FORMAT)
        self.etd_field.setDate(QDate.currentDate())

        self.dest_port_field = QLineEdit()
        self.dest_country_field = QLineEdit()
        self.quantity_field = SpinBox()
        self.quantity_field.setRange(1, 99)
        self.size_field = QLineEdit()
        self.pickup_field = QLineEdit()
        self.sequence_field = SpinBox()
        self.sequence_field.setRange(1, 9999)
        self.sequence_note = QLabel()
        # Plain text: folder names contain digit runs that Qt's auto-detection
        # renders as links, colouring parts of the name blue.
        self.sequence_note.setTextFormat(Qt.TextFormat.PlainText)
        self.sequence_note.setWordWrap(True)
        self.sequence_note.setStyleSheet(theme.style("font-size: 11px; color: #6b7280;"))
        self.shipping_field = QLineEdit()
        self.shipping_field.setPlaceholderText(
            "Terisi otomatis dari DO, contoh: EVERGREEN")

        for widget in (self.booking_field, self.vessel_field, self.voyage_field,
                       self.dest_port_field, self.dest_country_field,
                       self.size_field):
            widget.textChanged.connect(self._refresh)
        self.dest_country_field.textChanged.connect(self._update_quarantine)
        self.quantity_field.valueChanged.connect(self._refresh)
        self.shipping_field.textChanged.connect(self._refresh)

        form.addRow("Nomor booking", self.booking_field)
        form.addRow("Nama kapal", self.vessel_field)
        form.addRow("Voyage", self.voyage_field)
        form.addRow("ETD Belawan", self.etd_field)
        form.addRow("Pelabuhan tujuan", self.dest_port_field)
        form.addRow("Negara tujuan", self.dest_country_field)
        form.addRow("Jumlah kontainer", self.quantity_field)
        form.addRow("Ukuran kontainer", self.size_field)
        form.addRow("Lokasi ambil kontainer", self.pickup_field)
        form.addRow("Nomor urut", self.sequence_field)
        form.addRow("", self.sequence_note)
        form.addRow("Perusahaan pelayaran", self.shipping_field)

        scroll.setWidget(host)
        outer.addWidget(scroll, 1)
        return page

    def _populate_step2(self):
        data = self.extracted
        self.booking_field.setText(data.booking_number)
        self.vessel_field.setText(data.vessel_name)
        self.voyage_field.setText(data.voyage)
        if data.etd_belawan:
            self.etd_field.setDate(QDate(data.etd_belawan.year,
                                         data.etd_belawan.month,
                                         data.etd_belawan.day))
        self.dest_port_field.setText(data.destination_port)
        self.dest_country_field.setText(data.destination_country)
        self.quantity_field.setValue(max(1, data.container_quantity))
        self.size_field.setText(data.container_size_short)
        self.pickup_field.setText(data.empty_pickup_location)

        self._apply_carrier(data.carrier)
        self._detect_sequence()
        self._update_quarantine()
        self._start_permit_check()

    def _apply_carrier(self, carrier: str):
        """Fill the shipping company from the carrier on the DO letterhead.

        It is a free-text field: contacts are not configured, so there is no
        list to choose from, and the worker can correct it when the letterhead
        is unreadable (Yang Ming's DO names its carrier only in the logo).
        """
        if carrier:
            self.shipping_field.setText(carrier)
            self.carrier_message.show_success(
                f"Pelayaran terdeteksi dari DO: {carrier}.")
        else:
            self.shipping_field.clear()
            self.carrier_message.show_warning(
                "Pelayaran tidak dikenali dari dokumen DO. Isi manual di bawah.")

    def _resolve_source_folder(self) -> Path | None:
        """The template folder to copy, chosen by destination.

        Prefers a this-year folder whose name mentions the destination port,
        falling back to the last folder. Reads the live destination field so a
        manual edit re-points the copy source.
        """
        folder = drive.resolve_exporter_folder(
            self.ctx.drive_root, self.exporter, self.ctx.settings)
        if folder is None:
            return None
        year = self.etd_field.date().year()
        return drive.template_source_folder(
            folder, self.exporter, year,
            self.dest_port_field.text(), self.ctx.settings)

    def _detect_sequence(self):
        """PRD Section 5 — auto-detect, shown with the folder it came from."""
        folder = drive.resolve_exporter_folder(
            self.ctx.drive_root, self.exporter, self.ctx.settings)
        if folder is None:
            self.sequence_note.setText(
                f"Folder eksportir {self.exporter} tidak ditemukan di Google Drive.")
            self.source_folder = None
            return

        year = self.etd_field.date().year()
        latest = drive.latest_shipment_folder(
            folder, self.exporter, year, self.ctx.settings)
        next_sequence = drive.next_sequence_number(
            folder, self.exporter, year, self.ctx.settings)
        self.sequence_field.setValue(next_sequence)
        self.sequence_width = (drive.sequence_prefix_width(latest[1].name)
                               if latest else 1)
        self.source_folder = self._resolve_source_folder()
        if not latest:
            self.sequence_note.setText(
                f"Belum ada folder pengiriman {self.exporter} untuk tahun {year}.")
            return

        note = f"Nomor urut berikutnya: {next_sequence} (dari {latest[1].name})."
        src = self.source_folder
        if src is not None:
            dest = self.dest_port_field.text().strip()
            if dest and dest.lower() in src.name.lower():
                note += f" Menyalin dari folder tujuan {dest}: {src.name}."
            else:
                note += f" Menyalin dari folder terakhir: {src.name}."
        self.sequence_note.setText(note)

    def _update_quarantine(self):
        country = self.dest_country_field.text()
        if self.ctx.settings.is_quarantine_country(country):
            self.quarantine_message.show_warning(
                "Negara tujuan ini memerlukan pemeriksaan karantina. Ingat "
                "untuk menulis tanggal dan lokasi di SI yang dicetak.")
        else:
            self.quarantine_message.clear()
        self._refresh()

    def _start_permit_check(self):
        self.permit_message.show_info("Memeriksa izin impor...")
        worker = _PermitWorker(LLMClient.from_settings(self.ctx.settings),
                               self.source_folder)
        worker.done.connect(self._on_permit_done)
        self.workers.append(worker)
        worker.start()

    def _on_permit_done(self, level: str, message: str):
        getattr(self.permit_message, f"show_{level}")(message)

    # -- Step 3 -----------------------------------------------------------

    def _build_step3(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(theme.style(
            "background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px;"
            "padding: 14px; font-size: 13px; color: #374151;"))
        layout.addWidget(self.summary)

        self.plan = QLabel()
        self.plan.setWordWrap(True)
        self.plan.setTextFormat(Qt.TextFormat.PlainText)
        self.plan.setStyleSheet(theme.style("font-size: 12px; color: #6b7280;"))
        layout.addWidget(self.plan)

        self.create_progress = QProgressBar()
        self.create_progress.setRange(0, 0)
        self.create_progress.setVisible(False)
        layout.addWidget(self.create_progress)

        self.create_status = QLabel()
        self.create_status.setStyleSheet(theme.style("font-size: 12px; color: #374151;"))
        layout.addWidget(self.create_status)

        self.create_message = InlineMessage()
        layout.addWidget(self.create_message)

        self.after_row = QHBoxLayout()
        self.open_shipment_button = PrimaryButton("Buka Pengiriman")
        self.open_shipment_button.setVisible(False)
        self.open_folder_button = SecondaryButton("Buka Folder")
        self.open_folder_button.setVisible(False)
        self.open_folder_button.clicked.connect(
            lambda: self.created_folder
            and self.open_folder_requested.emit(str(self.created_folder)))
        self.after_row.addWidget(self.open_shipment_button)
        self.after_row.addWidget(self.open_folder_button)
        self.after_row.addStretch(1)
        layout.addLayout(self.after_row)

        layout.addStretch(1)
        return page

    def _populate_step3(self):
        # Re-pick the copy source from the final destination, in case it was
        # edited by hand after step 2 first ran.
        self.source_folder = self._resolve_source_folder()
        etd = self._etd_value()
        folder_name = self._folder_name()
        self.summary.setText(
            f"<b>{self.exporter}{self.sequence_field.value()}</b><br>"
            f"Booking {self.booking_field.text()}<br>"
            f"{self.vessel_field.text()} {self.voyage_field.text()}<br>"
            f"ETD {format_date_id(etd.isoformat())} · "
            f"Tujuan {self.dest_port_field.text()}, "
            f"{self.dest_country_field.text()}<br>"
            f"{self.quantity_field.value()} x {self.size_field.text()}")

        source = self.source_folder.name if self.source_folder else "(tidak ada)"
        self.plan.setText(
            "Yang akan dilakukan:\n"
            f"• Menyalin folder \"{source}\" menjadi \"{folder_name}\"\n"
            "• Mengosongkan subfolder\n"
            "• Mengganti nama file Excel dan invoice\n"
            "• Menyalin file DO ke folder baru\n"
            "• Mengisi Excel: VGM, SI, dan P.List\n"
            "• Mencetak lembar SI")

    def _etd_value(self) -> date:
        q = self.etd_field.date()
        return date(q.year(), q.month(), q.day())

    def _folder_name(self) -> str:
        return naming.folder_name(
            sequence=self.sequence_field.value(),
            container_quantity=self.quantity_field.value(),
            size_short=self.size_field.text().strip(),
            destination_port=self.dest_port_field.text(),
            booking_number=self.booking_field.text(),
            vessel_name=self.vessel_field.text(),
            voyage=self.voyage_field.text(),
            etd=self._etd_value(),
            sequence_width=getattr(self, 'sequence_width', 1))

    def _start_create(self):
        if self.source_folder is None:
            self.create_message.show_error(
                f"Tidak ada folder pengiriman sebelumnya untuk {self.exporter}. "
                "Buat folder template terlebih dahulu.")
            return

        self.next_button.setEnabled(False)
        self.back_button.setEnabled(False)
        self.create_progress.setVisible(True)
        self.create_message.clear()

        worker = _CreateWorker(
            source_folder=self.source_folder,
            target_parent=self.source_folder.parent,
            folder_name=self._folder_name(), do_pdf=self.pdf_path,
            sequence=self.sequence_field.value(), etd=self._etd_value(),
            booking=self.booking_field.text().strip(),
            vessel=self.vessel_field.text().strip(),
            voyage=self.voyage_field.text().strip(),
            quantity=self.quantity_field.value(),
            size_short=self.size_field.text().strip())
        worker.progress.connect(self.create_status.setText)
        worker.warnings.connect(self._on_prefill_warnings)
        worker.done.connect(self._on_create_done)
        self.workers.append(worker)
        worker.start()

    def _on_prefill_warnings(self, warnings: list):
        """Surface sheets the pre-fill could not adjust (PRD Section 10)."""
        if warnings:
            self.create_message.show_warning("\n".join(warnings))

    def _on_create_done(self, result, error: str):
        self.create_progress.setVisible(False)
        self.back_button.setEnabled(True)

        if result is None:
            self.create_message.show_error(error)
            self.next_button.setEnabled(True)
            return

        self.created_folder = result.destination
        shipment_id = self._record_shipment(result)

        if error:
            self.create_message.show_warning(error)
        elif self.create_message.isHidden():
            self.create_message.show_success("Pengiriman berhasil dibuat.")

        self.create_status.setText("Mencetak SI...")
        print_result = printing.print_si(result.main_workbook) \
            if result.main_workbook else None

        if print_result and print_result.printed:
            self.create_status.setText("SI dicetak.")
            # PRD Section 5 — B2 only auto-completes on a successful print.
            self.shipments.set_step(shipment_id, "B2", True, source="auto")
        elif print_result:
            self.create_status.setText(print_result.message)

        self.next_button.setVisible(False)
        self.open_shipment_button.setVisible(True)
        self.open_folder_button.setVisible(True)
        try:
            self.open_shipment_button.clicked.disconnect()
        except TypeError:
            pass
        self.open_shipment_button.clicked.connect(
            lambda: self.created.emit(shipment_id))

    def _record_shipment(self, result) -> str:
        etd = self._etd_value()
        shipment_id = self.shipments.create({
            "exporter_code": self.exporter,
            "sequence_number": self.sequence_field.value(),
            "booking_number": self.booking_field.text().strip(),
            "vessel_name": self.vessel_field.text().strip(),
            "voyage": self.voyage_field.text().strip(),
            "etd_belawan": etd.isoformat(),
            "destination_port": self.dest_port_field.text().strip(),
            "destination_country": self.dest_country_field.text().strip(),
            "container_quantity": self.quantity_field.value(),
            "container_size_short": self.size_field.text().strip(),
            "empty_pickup_location": self.pickup_field.text().strip(),
            "quarantine_required": self.ctx.settings.is_quarantine_country(
                self.dest_country_field.text()),
            "folder_path": str(result.destination),
            "do_pdf_filename": self.pdf_path.name if self.pdf_path else None,
            "shipping_company": self.shipping_field.text().strip() or None,
        })
        # PRD 6.3 — A1 and B1 auto-complete on creation.
        self.shipments.set_step(shipment_id, "A1", True, source="auto")
        self.shipments.set_step(shipment_id, "B1", True, source="auto")
        return shipment_id

    # -- navigation --------------------------------------------------------

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(theme.style("font-size: 13px; font-weight: 600; color: #374151;"))
        return label

    def _step(self) -> int:
        return self.stack.currentIndex()

    def _can_advance(self) -> bool:
        step = self._step()
        if step == 0:
            return self.exporter is not None and self.pdf_path is not None
        if step == 1:
            return all([
                self.booking_field.text().strip(),
                self.vessel_field.text().strip(),
                self.voyage_field.text().strip(),
                self.dest_port_field.text().strip(),
                self.dest_country_field.text().strip(),
                self.size_field.text().strip(),
                # Shipping company is optional: contacts are not configured, so
                # the dropdown is usually empty (user decision, 2026-07-20).
            ])
        return True

    def reset(self):
        """Return the wizard to a clean step 1.

        The wizard is a long-lived widget, so without this it keeps the
        previous run's state: most visibly the success screen hides
        `next_button` permanently, leaving the next shipment unable to advance
        past step 1 (reported 2026-07-22). It also kept the old DO file,
        exporter and messages.
        """
        if not hasattr(self, "next_button"):
            return
        self.exporter = None
        for card in self.exporter_cards.values():
            card.set_selected(False)
        self._choose_pdf(None)
        self.extracted = ExtractedDO()

        for message in (self.step1_message, self.extract_message,
                        self.carrier_message, self.quarantine_message,
                        self.permit_message, self.create_message):
            message.clear()
        self.create_status.setText("")
        self.create_progress.setVisible(False)

        # Undo the success screen.
        self.next_button.setVisible(True)
        self.open_shipment_button.setVisible(False)
        self.open_folder_button.setVisible(False)
        self.back_button.setEnabled(True)

        self.stack.setCurrentIndex(0)
        self._refresh()

    def _refresh(self):
        if not hasattr(self, "next_button"):
            return
        step = self._step()
        titles = ["Pilih Eksportir & Unggah DO", "Periksa Data DO",
                  "Konfirmasi & Buat"]
        self.title.setText(titles[step])
        self.step_label.setText(f"Langkah {step + 1} dari 3")
        self.back_button.setVisible(step > 0)
        self.next_button.setText("Buat Pengiriman" if step == 2 else "Lanjut")
        self.next_button.setEnabled(self._can_advance())

    def _go_back(self):
        if self._step() > 0:
            self.stack.setCurrentIndex(self._step() - 1)
            self._refresh()

    def _go_next(self):
        step = self._step()
        if step == 0:
            self._start_extraction()
        elif step == 1:
            self._populate_step3()
            self.stack.setCurrentIndex(2)
            self._refresh()
        else:
            self._start_create()

    def _start_extraction(self):
        self.next_button.setEnabled(False)
        self.next_button.setText("Membaca DO...")
        worker = _ExtractionWorker(
            LLMClient.from_settings(self.ctx.settings), self.pdf_path)
        worker.done.connect(self._on_extraction_done)
        self.workers.append(worker)
        worker.start()

    def _on_extraction_done(self, extracted, error: str):
        self.next_button.setText("Lanjut")
        # PRD Section 5 — extraction failure advances anyway, with empty fields.
        self.extracted = extracted or ExtractedDO()
        self._populate_step2()
        self.stack.setCurrentIndex(1)

        if error:
            self.extract_message.show_error(
                f"{error} Isi data secara manual di bawah ini.")
        elif self.extracted.missing:
            self.extract_message.show_warning(
                "Sebagian data tidak dapat diambil otomatis. "
                "Lengkapi secara manual di bawah ini.")
        else:
            self.extract_message.show_success("Data DO berhasil dibaca.")
        self._refresh()

    def shutdown(self, timeout_ms: int = 15000):
        """Wait for background workers to finish.

        Extraction and the permit check hold PDFs open while they run; letting
        the widget go away with threads still running leaves files locked and
        risks a crash when a finished worker signals a deleted receiver.
        """
        for worker in self.workers:
            if worker.isRunning():
                worker.wait(timeout_ms)
        self.workers.clear()

    def _cancel(self):
        answer = QMessageBox.question(
            self, "Batalkan pengaturan pengiriman?",
            "Pengaturan pengiriman yang belum disimpan akan hilang. Lanjutkan?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.cancelled.emit()
