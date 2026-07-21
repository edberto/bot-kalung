"""First-launch setup wizard (PRD Section 3). All UI text is Bahasa Indonesia."""

from __future__ import annotations

from . import theme


from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QFileDialog, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from ..core.constants import APP_NAME, EXPORTERS, WORKER_EMAILS
from ..core.context import AppContext
from ..services.drive import validate_drive_root
from ..services.llm import LLMClient
from .widgets import ComboBox, InlineMessage, PrimaryButton, SecondaryButton


class _ConnectionTest(QThread):
    """Runs the LLM test off the UI thread so the window stays responsive."""

    finished_with = pyqtSignal(bool, str)

    def __init__(self, client: LLMClient):
        super().__init__()
        self.client = client

    def run(self):
        ok, message = self.client.test_connection()
        self.finished_with.emit(ok, message)


class SetupWizard(QWidget):
    """Full-screen 3-step overlay. Cannot be dismissed until complete."""

    setup_complete = pyqtSignal()

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.drive_root: str | None = None
        self.llm_verified = False
        self.test_thread: _ConnectionTest | None = None

        self.setWindowTitle(f"{APP_NAME} — Pengaturan Awal")
        self.setMinimumSize(760, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(48, 40, 48, 32)
        root.setSpacing(16)

        self.heading = QLabel()
        self.heading.setStyleSheet(theme.style("font-size: 22px; font-weight: 600;"))
        self.step_label = QLabel()
        self.step_label.setStyleSheet(theme.style("color: #6b7280;"))
        root.addWidget(self.heading)
        root.addWidget(self.step_label)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(theme.style("color: #e5e7eb;"))
        root.addWidget(divider)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_step1())
        self.stack.addWidget(self._build_step2())
        self.stack.addWidget(self._build_step3())
        root.addWidget(self.stack, 1)

        nav = QHBoxLayout()
        self.back_button = SecondaryButton("Kembali")
        self.back_button.clicked.connect(self._go_back)
        self.skip_link = QPushButton("Lewati untuk sekarang")
        self.skip_link.setFlat(True)
        self.skip_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.skip_link.setStyleSheet(theme.style(
            "border: none; color: #6b7280; text-decoration: underline;"))
        self.skip_link.clicked.connect(self._skip_llm)
        self.next_button = PrimaryButton("Lanjut")
        self.next_button.clicked.connect(self._go_next)

        nav.addWidget(self.back_button)
        nav.addStretch(1)
        nav.addWidget(self.skip_link)
        nav.addWidget(self.next_button)
        root.addLayout(nav)

        self._refresh_step()

    # -- Step 1: Google Drive path --------------------------------------

    def _build_step1(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        layout.addWidget(QLabel(
            "Pilih folder utama Google Drive yang tersinkronisasi di komputer ini."))

        row = QHBoxLayout()
        self.path_field = QLineEdit()
        self.path_field.setReadOnly(True)
        self.path_field.setPlaceholderText("Belum ada folder dipilih")
        browse = SecondaryButton("Pilih Folder...")
        browse.clicked.connect(self._pick_drive_folder)
        row.addWidget(self.path_field, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        self.path_message = InlineMessage()
        layout.addWidget(self.path_message)

        hint = QLabel(
            "Folder harus berisi minimal satu folder eksportir "
            f"({', '.join(EXPORTERS)}). Nama folder yang berbeda dapat "
            "dipetakan nanti di Pengaturan.")
        hint.setWordWrap(True)
        hint.setStyleSheet(theme.style("color: #6b7280; font-size: 12px;"))
        layout.addWidget(hint)

        layout.addStretch(1)
        return page

    def _pick_drive_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self, "Pilih folder Google Drive")
        if not selected:
            return
        self.path_field.setText(selected)
        is_valid, found = validate_drive_root(selected)
        if is_valid:
            self.drive_root = selected
            self.path_message.show_success(
                f"Folder eksportir ditemukan: {', '.join(found)}")
        else:
            self.drive_root = None
            self.path_message.show_error(
                "Tidak ditemukan folder eksportir di lokasi ini. "
                "Pilih folder utama Google Drive yang benar.")
        self._refresh_step()

    # -- Step 2: LLM provider -------------------------------------------

    def _build_step2(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        self.provider_group = QButtonGroup(self)

        anthropic_box = QGroupBox()
        anthropic_layout = QVBoxLayout(anthropic_box)
        self.radio_anthropic = QRadioButton("Claude API (Anthropic) — disarankan")
        self.radio_anthropic.setChecked(True)
        self.provider_group.addButton(self.radio_anthropic)
        anthropic_layout.addWidget(self.radio_anthropic)
        self.api_key_field = QLineEdit()
        self.api_key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_field.setPlaceholderText("Kunci API (sk-ant-...)")
        anthropic_layout.addWidget(self.api_key_field)
        note = QLabel(
            "Dapatkan kunci API di console.anthropic.com\n"
            "Sekitar $0,001 per pengiriman. Terpisah dari langganan Claude.ai.")
        note.setStyleSheet(theme.style("color: #6b7280; font-size: 12px;"))
        anthropic_layout.addWidget(note)
        layout.addWidget(anthropic_box)

        ollama_box = QGroupBox()
        ollama_layout = QVBoxLayout(ollama_box)
        self.radio_ollama = QRadioButton("LLM Lokal (Ollama) — gratis, tanpa internet")
        self.provider_group.addButton(self.radio_ollama)
        ollama_layout.addWidget(self.radio_ollama)
        self.ollama_model_field = QLineEdit("llama3")
        self.ollama_model_field.setPlaceholderText("Nama model (contoh: llama3)")
        ollama_layout.addWidget(self.ollama_model_field)
        ollama_note = QLabel("Ollama harus sudah terpasang dan berjalan di komputer ini.")
        ollama_note.setStyleSheet(theme.style("color: #6b7280; font-size: 12px;"))
        ollama_layout.addWidget(ollama_note)
        layout.addWidget(ollama_box)

        self.radio_anthropic.toggled.connect(self._on_provider_changed)
        self.api_key_field.textChanged.connect(self._invalidate_llm_test)
        self.ollama_model_field.textChanged.connect(self._invalidate_llm_test)

        self.test_button = SecondaryButton("Uji Koneksi")
        self.test_button.clicked.connect(self._test_connection)
        layout.addWidget(self.test_button)

        self.llm_message = InlineMessage()
        layout.addWidget(self.llm_message)

        layout.addStretch(1)
        self._on_provider_changed()
        return page

    def _on_provider_changed(self):
        use_anthropic = self.radio_anthropic.isChecked()
        self.api_key_field.setEnabled(use_anthropic)
        self.ollama_model_field.setEnabled(not use_anthropic)
        self._invalidate_llm_test()

    def _invalidate_llm_test(self):
        self.llm_verified = False
        self.llm_message.clear()
        self._refresh_step()

    def _current_llm_client(self) -> LLMClient:
        if self.radio_anthropic.isChecked():
            return LLMClient("anthropic", api_key=self.api_key_field.text().strip())
        return LLMClient("ollama", ollama_model=self.ollama_model_field.text().strip())

    def _test_connection(self):
        self.test_button.setEnabled(False)
        self.llm_message.show_info("Menguji koneksi...")
        self.test_thread = _ConnectionTest(self._current_llm_client())
        self.test_thread.finished_with.connect(self._on_test_finished)
        self.test_thread.start()

    def _on_test_finished(self, ok: bool, message: str):
        self.test_button.setEnabled(True)
        self.llm_verified = ok
        if ok:
            self.llm_message.show_success(message)
        else:
            self.llm_message.show_error(message)
        self._refresh_step()

    def _skip_llm(self):
        """PRD Section 3 Step 2 — explicit opt-out past the connection test."""
        self.llm_verified = True
        self.llm_message.show_warning(
            "Dilewati. Ekstraksi otomatis DO tidak akan berfungsi sampai "
            "koneksi diatur di Pengaturan.")
        self.stack.setCurrentIndex(2)
        self._refresh_step()

    # -- Step 3: Identity & contacts ------------------------------------

    def _build_step3(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        identity_box = QGroupBox("Email saya")
        identity_layout = QVBoxLayout(identity_box)
        self.email_combo = ComboBox()
        self.email_combo.addItem("— Pilih email Anda —", "")
        for email in WORKER_EMAILS:
            self.email_combo.addItem(email, email)
        self.email_combo.currentIndexChanged.connect(self._refresh_step)
        identity_layout.addWidget(self.email_combo)

        self.identity_note = QLabel(
            "Dua email tim lainnya otomatis dimasukkan ke kolom To setiap draf "
            "email. Penerima luar diisi manual di Gmail.")
        self.identity_note.setWordWrap(True)
        self.identity_note.setStyleSheet(
            theme.style("color: #6b7280; font-size: 12px;"))
        identity_layout.addWidget(self.identity_note)
        layout.addWidget(identity_box)

        layout.addStretch(1)
        return page

    # -- navigation ------------------------------------------------------

    def _step(self) -> int:
        return self.stack.currentIndex()

    def _can_advance(self) -> bool:
        step = self._step()
        if step == 0:
            return self.drive_root is not None
        if step == 1:
            return self.llm_verified
        return bool(self.email_combo.currentData())

    def _refresh_step(self):
        # Page builders emit change signals while the nav row is still being
        # constructed; ignore those early calls.
        if not hasattr(self, "next_button"):
            return
        step = self._step()
        titles = [
            ("Folder Google Drive", "Langkah 1 dari 3"),
            ("Penyedia LLM", "Langkah 2 dari 3"),
            ("Identitas", "Langkah 3 dari 3"),
        ]
        self.heading.setText(titles[step][0])
        self.step_label.setText(titles[step][1])

        self.back_button.setVisible(step > 0)
        self.skip_link.setVisible(step == 1 and not self.llm_verified)
        self.next_button.setText("Selesai" if step == 2 else "Lanjut")
        self.next_button.setEnabled(self._can_advance())

    def _go_back(self):
        if self._step() > 0:
            self.stack.setCurrentIndex(self._step() - 1)
            self._refresh_step()

    def _go_next(self):
        step = self._step()
        if step == 0:
            self.ctx.create(self.drive_root)
            self.stack.setCurrentIndex(1)
        elif step == 1:
            self._save_llm_settings()
            self.stack.setCurrentIndex(2)
        else:
            self._finish()
            return
        self._refresh_step()

    def _save_llm_settings(self):
        if self.radio_anthropic.isChecked():
            self.ctx.settings.set_many({
                "llm_provider": "anthropic",
                "llm_api_key": self.api_key_field.text().strip(),
            })
        else:
            self.ctx.settings.set_many({
                "llm_provider": "ollama",
                "ollama_model": self.ollama_model_field.text().strip() or "llama3",
            })

    def _finish(self):
        self.ctx.settings.set("my_email", self.email_combo.currentData())
        self.ctx.settings.set("setup_complete", "1")
        self.setup_complete.emit()
