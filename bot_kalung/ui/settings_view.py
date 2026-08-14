"""Settings (PRD Section 11).

Four tabs rather than the PRD's five: the Contacts tab is gone, since contacts
are not configured (see DECISIONS.md section 13). The Message Templates tab
edits subjects only, because bodies are deliberately empty.

A General tab addition not in the PRD: the exporter -> Drive folder mapping,
which is needed because only AMJ's folder is named after its exporter code.
"""

from __future__ import annotations

from . import theme

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QRadioButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..core.constants import (
    DEFAULT_EXPORTER_FOLDERS, EXPORTERS, NTFY_DEFAULT_SERVER, NTFY_TOPIC,
    WORKER_EMAILS,
)
from ..services import drive
from ..services.llm import LLMClient
from .widgets import (
    ComboBox, InlineMessage, PrimaryButton, SecondaryButton, SpinBox,
)


class _ConnectionTest(QThread):
    finished_with = pyqtSignal(bool, str)

    def __init__(self, client: LLMClient):
        super().__init__()
        self.client = client

    def run(self):
        ok, message = self.client.test_connection()
        self.finished_with.emit(ok, message)


class SettingsView(QWidget):
    saved = pyqtSignal()
    back_requested = pyqtSignal()
    theme_changed = pyqtSignal(str)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.test_thread: _ConnectionTest | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Pengaturan")
        title.setStyleSheet(theme.style("font-size: 22px; font-weight: 700; color: #111827;"))
        header.addWidget(title)
        header.addStretch(1)
        back = SecondaryButton("Kembali")
        back.clicked.connect(self.back_requested)
        header.addWidget(back)
        outer.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._scroll_wrap(self._build_general()), "Umum")
        self.tabs.addTab(self._scroll_wrap(self._build_llm()), "LLM")
        self.tabs.addTab(self._scroll_wrap(self._build_templates()), "Subjek Email")
        self.tabs.addTab(self._scroll_wrap(self._build_quarantine()),
                         "Negara Karantina")
        outer.addWidget(self.tabs, 1)

        self.message = InlineMessage()
        outer.addWidget(self.message)

        footer = QHBoxLayout()
        footer.addStretch(1)
        save = PrimaryButton("Simpan")
        save.clicked.connect(self.save)
        footer.addWidget(save)
        outer.addLayout(footer)

        self.load()

    @staticmethod
    def _scroll_wrap(page: QWidget) -> QScrollArea:
        """Put a tab page in a vertical scroll area so a tall tab (the General
        one) is fully reachable instead of being clipped by the tab height.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    # -- Tab 1: General ----------------------------------------------------

    def _build_general(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        drive_box = QGroupBox("Folder Google Drive")
        drive_layout = QVBoxLayout(drive_box)
        row = QHBoxLayout()
        self.drive_field = QLineEdit()
        self.drive_field.setReadOnly(True)
        browse = SecondaryButton("Pilih Folder...")
        browse.clicked.connect(self._pick_drive)
        validate = SecondaryButton("Periksa")
        validate.clicked.connect(self._validate_drive)
        row.addWidget(self.drive_field, 1)
        row.addWidget(browse)
        row.addWidget(validate)
        drive_layout.addLayout(row)
        self.drive_message = InlineMessage()
        drive_layout.addWidget(self.drive_message)
        layout.addWidget(drive_box)

        folders_box = QGroupBox("Folder per eksportir")
        folders_layout = QFormLayout(folders_box)
        note = QLabel(
            "Nama folder di Google Drive untuk tiap eksportir. Hanya AMJ yang "
            "namanya sama dengan kode eksportir.")
        note.setWordWrap(True)
        note.setStyleSheet(theme.style("color: #6b7280; font-size: 11px;"))
        folders_layout.addRow(note)
        self.folder_fields: dict[str, QLineEdit] = {}
        for code in EXPORTERS:
            field = QLineEdit()
            field.setPlaceholderText(DEFAULT_EXPORTER_FOLDERS.get(code, code))
            self.folder_fields[code] = field
            folders_layout.addRow(code, field)
        layout.addWidget(folders_box)

        theme_box = QGroupBox("Tampilan")
        theme_layout = QFormLayout(theme_box)
        self.theme_combo = ComboBox()
        self.theme_combo.addItem("Terang", "light")
        self.theme_combo.addItem("Gelap", "dark")
        theme_layout.addRow("Tema", self.theme_combo)
        theme_note = QLabel("Perubahan tema diterapkan setelah menekan Simpan.")
        theme_note.setStyleSheet(theme.style("color: #6b7280; font-size: 11px;"))
        theme_layout.addRow("", theme_note)
        layout.addWidget(theme_box)

        identity_box = QGroupBox("Identitas")
        identity_layout = QFormLayout(identity_box)
        self.email_combo = ComboBox()
        for email in WORKER_EMAILS:
            self.email_combo.addItem(email, email)
        identity_layout.addRow("Email saya", self.email_combo)

        team = QLabel("\n".join(WORKER_EMAILS))
        team.setStyleSheet(theme.style("color: #6b7280; font-size: 11px;"))
        identity_layout.addRow("Email tim", team)
        hint = QLabel(
            "Email tim selain milik Anda otomatis dimasukkan ke kolom To "
            "setiap draf email.")
        hint.setWordWrap(True)
        hint.setStyleSheet(theme.style("color: #6b7280; font-size: 11px;"))
        identity_layout.addRow("", hint)
        layout.addWidget(identity_box)

        monitor_box = QGroupBox("Pemantauan BNCT")
        monitor_layout = QFormLayout(monitor_box)
        self.bnct_interval_field = SpinBox()
        self.bnct_interval_field.setRange(1, 120)
        self.bnct_interval_field.setSuffix(" menit")
        monitor_layout.addRow("Interval pemeriksaan", self.bnct_interval_field)
        monitor_note = QLabel(
            "Aplikasi memeriksa jadwal & status kapal di BNCT selama aplikasi "
            "terbuka. Pemeriksaan berhenti setelah langkah D2 ditandai selesai.")
        monitor_note.setWordWrap(True)
        monitor_note.setStyleSheet(theme.style("color: #6b7280; font-size: 11px;"))
        monitor_layout.addRow("", monitor_note)
        layout.addWidget(monitor_box)

        ntfy_box = QGroupBox("Notifikasi ntfy")
        ntfy_layout = QFormLayout(ntfy_box)
        self.ntfy_enabled = QCheckBox("Kirim notifikasi ke HP lewat ntfy")
        ntfy_layout.addRow("", self.ntfy_enabled)
        self.ntfy_server_field = QLineEdit()
        self.ntfy_server_field.textChanged.connect(self._update_ntfy_url)
        ntfy_layout.addRow("Server", self.ntfy_server_field)
        self.ntfy_url_label = QLabel()
        self.ntfy_url_label.setWordWrap(True)
        self.ntfy_url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ntfy_url_label.setStyleSheet(theme.style(
            "color: #2563eb; font-size: 12px; font-weight: 600;"))
        ntfy_layout.addRow("Langganan", self.ntfy_url_label)
        ntfy_note = QLabel(
            "Berlangganan topik di atas pada aplikasi ntfy (Android/iOS) untuk "
            "menerima notifikasi kapal di HP walau aplikasi ini tertutup. Topik "
            "ini sama untuk semua pengguna.")
        ntfy_note.setWordWrap(True)
        ntfy_note.setStyleSheet(theme.style("color: #6b7280; font-size: 11px;"))
        ntfy_layout.addRow("", ntfy_note)
        layout.addWidget(ntfy_box)

        layout.addStretch(1)
        return page

    def _update_ntfy_url(self):
        server = (self.ntfy_server_field.text().strip()
                  or NTFY_DEFAULT_SERVER).rstrip("/")
        self.ntfy_url_label.setText(f"{server}/{NTFY_TOPIC}")

    def _pick_drive(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Pilih folder Google Drive", self.drive_field.text())
        if chosen:
            self.drive_field.setText(chosen)
            self._validate_drive()

    def _validate_drive(self):
        path = self.drive_field.text().strip()
        if not path or not Path(path).is_dir():
            self.drive_message.show_error(
                "Folder Google Drive tidak ditemukan atau tidak dapat diakses. "
                "Perbarui path di Pengaturan.")
            return False
        ok, found = drive.validate_drive_root(path, self.ctx.settings)
        if ok:
            self.drive_message.show_success(
                f"Folder eksportir ditemukan: {', '.join(found)}")
        else:
            self.drive_message.show_warning(
                "Tidak ditemukan folder eksportir di lokasi ini. Periksa "
                "pemetaan folder di bawah.")
        return ok

    # -- Tab 2: LLM --------------------------------------------------------

    def _build_llm(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        self.provider_group = QButtonGroup(self)
        anthropic_box = QGroupBox()
        anthropic_layout = QVBoxLayout(anthropic_box)
        self.radio_anthropic = QRadioButton("Claude API (Anthropic)")
        self.provider_group.addButton(self.radio_anthropic)
        anthropic_layout.addWidget(self.radio_anthropic)
        self.api_key_field = QLineEdit()
        self.api_key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_field.setPlaceholderText("Kunci API (sk-ant-...)")
        anthropic_layout.addWidget(self.api_key_field)
        layout.addWidget(anthropic_box)

        ollama_box = QGroupBox()
        ollama_layout = QFormLayout(ollama_box)
        self.radio_ollama = QRadioButton("LLM Lokal (Ollama)")
        self.provider_group.addButton(self.radio_ollama)
        ollama_layout.addRow(self.radio_ollama)
        self.ollama_model_field = QLineEdit()
        self.ollama_url_field = QLineEdit()
        ollama_layout.addRow("Model", self.ollama_model_field)
        ollama_layout.addRow("URL", self.ollama_url_field)
        layout.addWidget(ollama_box)

        self.radio_anthropic.toggled.connect(self._on_provider_changed)

        self.test_button = SecondaryButton("Uji Koneksi")
        self.test_button.clicked.connect(self._test_connection)
        layout.addWidget(self.test_button)

        self.llm_message = InlineMessage()
        layout.addWidget(self.llm_message)
        layout.addStretch(1)
        return page

    def _on_provider_changed(self):
        use_anthropic = self.radio_anthropic.isChecked()
        self.api_key_field.setEnabled(use_anthropic)
        self.ollama_model_field.setEnabled(not use_anthropic)
        self.ollama_url_field.setEnabled(not use_anthropic)

    def _current_client(self) -> LLMClient:
        if self.radio_anthropic.isChecked():
            return LLMClient("anthropic", api_key=self.api_key_field.text().strip())
        return LLMClient("ollama",
                         ollama_model=self.ollama_model_field.text().strip(),
                         ollama_url=self.ollama_url_field.text().strip())

    def _test_connection(self):
        self.test_button.setEnabled(False)
        self.llm_message.show_info("Menguji koneksi...")
        self.test_thread = _ConnectionTest(self._current_client())
        self.test_thread.finished_with.connect(self._on_test_finished)
        self.test_thread.start()

    def _on_test_finished(self, ok: bool, message: str):
        self.test_button.setEnabled(True)
        (self.llm_message.show_success if ok else self.llm_message.show_error)(message)

    # -- Tab 3: Email subjects ---------------------------------------------

    def _build_templates(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        note = QLabel(
            "Isi email dikosongkan dengan sengaja — ditulis manual di Gmail. "
            "Hanya subjek yang disiapkan. Variabel yang tersedia: "
            "{exporter} {seq} {booking_no} {vessel} {voyage} {vessel_voyage} "
            "{etd} {etd_short} {destination} {destination_country} "
            "{container_qty} {container_size}")
        note.setWordWrap(True)
        note.setStyleSheet(theme.style("color: #6b7280; font-size: 11px;"))
        layout.addWidget(note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        form = QFormLayout(host)
        self.subject_fields: dict[str, QLineEdit] = {}
        rows = self.ctx.db.query(
            "SELECT id, step_code, subject_template FROM message_templates "
            "WHERE channel='email' ORDER BY id")
        for row in rows:
            field = QLineEdit(row["subject_template"] or "")
            self.subject_fields[row["id"]] = field
            form.addRow(f"{row['id']} · {row['step_code']}", field)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        return page

    # -- Tab 4: Quarantine countries ---------------------------------------

    def _build_quarantine(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        note = QLabel(
            "Negara tujuan yang memerlukan pemeriksaan karantina. Diperiksa "
            "pada langkah 2 pembuatan pengiriman.")
        note.setWordWrap(True)
        note.setStyleSheet(theme.style("color: #6b7280; font-size: 11px;"))
        layout.addWidget(note)

        self.country_list = QListWidget()
        self.country_list.setMaximumHeight(220)
        layout.addWidget(self.country_list)

        row = QHBoxLayout()
        self.country_field = QLineEdit()
        self.country_field.setPlaceholderText("Nama negara, contoh: PAKISTAN")
        self.country_field.returnPressed.connect(self._add_country)
        add = SecondaryButton("Tambah")
        add.clicked.connect(self._add_country)
        remove = SecondaryButton("Hapus yang dipilih")
        remove.clicked.connect(self._remove_country)
        row.addWidget(self.country_field, 1)
        row.addWidget(add)
        row.addWidget(remove)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _add_country(self):
        name = self.country_field.text().strip().upper()
        if not name:
            return
        existing = {self.country_list.item(i).text()
                    for i in range(self.country_list.count())}
        if name in existing:
            self.message.show_warning(f"{name} sudah ada dalam daftar.")
            return
        self.country_list.addItem(QListWidgetItem(name))
        self.country_field.clear()

    def _remove_country(self):
        for item in self.country_list.selectedItems():
            self.country_list.takeItem(self.country_list.row(item))

    # -- load / save --------------------------------------------------------

    def load(self):
        settings = self.ctx.settings
        self.drive_field.setText(settings.get("google_drive_root") or "")

        mapping = settings.get("exporter_folders") or {}
        for code, field in self.folder_fields.items():
            field.setText(mapping.get(code, ""))

        mine = settings.get("my_email") or ""
        index = self.email_combo.findData(mine)
        if index >= 0:
            self.email_combo.setCurrentIndex(index)

        theme_index = self.theme_combo.findData(settings.get("theme") or "light")
        if theme_index >= 0:
            self.theme_combo.setCurrentIndex(theme_index)

        provider = settings.get("llm_provider") or "anthropic"
        self.radio_anthropic.setChecked(provider != "ollama")
        self.radio_ollama.setChecked(provider == "ollama")
        self.api_key_field.setText(settings.get("llm_api_key") or "")
        self.ollama_model_field.setText(settings.get("ollama_model") or "llama3")
        self.ollama_url_field.setText(
            settings.get("ollama_url") or "http://localhost:11434")
        self._on_provider_changed()

        self.country_list.clear()
        for country in settings.quarantine_countries:
            self.country_list.addItem(QListWidgetItem(country))

        try:
            interval = int(settings.get("bnct_interval_minutes", 5))
        except (TypeError, ValueError):
            interval = 5
        self.bnct_interval_field.setValue(max(1, interval))

        self.ntfy_enabled.setChecked(settings.get_bool("ntfy_enabled"))
        self.ntfy_server_field.setText(
            settings.get("ntfy_server") or NTFY_DEFAULT_SERVER)
        self._update_ntfy_url()

    def save(self):
        settings = self.ctx.settings

        # Only store an exporter folder when it differs from the built-in
        # default, so the defaults keep applying if they are ever corrected.
        mapping = {}
        for code, field in self.folder_fields.items():
            value = field.text().strip()
            if value and value != DEFAULT_EXPORTER_FOLDERS.get(code):
                mapping[code] = value

        countries = [self.country_list.item(i).text()
                     for i in range(self.country_list.count())]

        theme_changed = (self.theme_combo.currentData()
                         != (settings.get("theme") or "light"))

        settings.set_many({
            "theme": self.theme_combo.currentData() or "light",
            "google_drive_root": self.drive_field.text().strip(),
            "exporter_folders": mapping,
            "bnct_interval_minutes": str(self.bnct_interval_field.value()),
            "ntfy_enabled": self.ntfy_enabled.isChecked(),
            "ntfy_server": self.ntfy_server_field.text().strip()
                           or NTFY_DEFAULT_SERVER,
            "my_email": self.email_combo.currentData() or "",
            "llm_provider": "anthropic" if self.radio_anthropic.isChecked()
                            else "ollama",
            "llm_api_key": self.api_key_field.text().strip(),
            "ollama_model": self.ollama_model_field.text().strip() or "llama3",
            "ollama_url": self.ollama_url_field.text().strip()
                          or "http://localhost:11434",
            "quarantine_countries": countries,
        })

        for template_id, field in self.subject_fields.items():
            self.ctx.db.execute(
                "UPDATE message_templates SET subject_template=? WHERE id=?",
                (field.text().strip(), template_id))

        # Keep the live Drive root in step with what was saved.
        root = self.drive_field.text().strip()
        if root and root != str(self.ctx.drive_root or ""):
            self.ctx.drive_root = root

        self.message.show_success("Pengaturan disimpan.")
        self.saved.emit()
        if theme_changed:
            # Styles are applied when a widget is built, so the window is
            # rebuilt rather than trying to restyle every widget in place.
            self.theme_changed.emit(self.theme_combo.currentData() or "light")

    def shutdown(self, timeout_ms: int = 15000):
        if self.test_thread is not None and self.test_thread.isRunning():
            self.test_thread.wait(timeout_ms)
