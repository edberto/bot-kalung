"""The folder-scan screen.

The shipment-creation wizard was removed; active shipments are discovered by
scanning the Drive instead. This screen shows the scan status and the discovery
report, and lets the worker trigger a scan on demand. The scans themselves run
on a timer in ScanController; the imported shipments appear on the dashboard.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QTextEdit, QVBoxLayout, QWidget,
)

from . import theme
from .widgets import InlineMessage, PrimaryButton


class ScanView(QWidget):
    scan_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Pindai Folder Pengiriman")
        title.setStyleSheet(theme.style(
            "font-size: 22px; font-weight: 700; color: #111827;"))
        header.addWidget(title)
        header.addStretch(1)
        self.scan_button = PrimaryButton("Pindai Sekarang")
        self.scan_button.clicked.connect(self.scan_requested)
        header.addWidget(self.scan_button)
        layout.addLayout(header)

        note = QLabel(
            "Pengiriman aktif ditemukan otomatis dengan memindai folder Google "
            "Drive setiap beberapa menit. Pengiriman yang sudah punya scan Bill "
            "of Lading dianggap selesai dan tidak dilacak. Hasilnya muncul di "
            "Beranda.")
        note.setWordWrap(True)
        note.setStyleSheet(theme.style("font-size: 12px; color: #6b7280;"))
        layout.addWidget(note)

        self.message = InlineMessage()
        layout.addWidget(self.message)

        report_label = QLabel("Laporan pindai terakhir")
        report_label.setStyleSheet(theme.style(
            "font-size: 13px; font-weight: 600; color: #374151;"))
        layout.addWidget(report_label)

        self.report = QTextEdit()
        self.report.setReadOnly(True)
        self.report.setPlaceholderText("Belum ada pindai pada sesi ini.")
        self.report.setStyleSheet(theme.style(
            "background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px;"
            "font-size: 12px; color: #374151; padding: 8px;"))
        layout.addWidget(self.report, 1)

    # -- driven by the controller / main window ---------------------------

    def set_scanning(self, scanning: bool) -> None:
        self.scan_button.setEnabled(not scanning)
        self.scan_button.setText("Memindai..." if scanning else "Pindai Sekarang")
        if scanning:
            self.message.show_info("Memindai folder Google Drive...")

    def show_result(self, result) -> None:
        self.set_scanning(False)
        if result.imported or result.completed:
            self.message.show_success(f"Pindai selesai — {result.summary}.")
        else:
            self.message.show_info("Pindai selesai — tidak ada pengiriman baru.")

        lines: list[str] = []
        if result.imported:
            lines.append("Pengiriman baru: " + ", ".join(result.imported))
        if result.completed:
            lines.append("Sudah selesai (dilewati): " + ", ".join(result.completed))
        if result.report:
            lines.append("")
            lines.append("Folder yang dipindai:")
            lines += [f"  • {note}" for note in result.report]
        if result.warnings:
            lines.append("")
            lines.append("Peringatan:")
            lines += [f"  • {warn}" for warn in result.warnings]
        self.report.setPlainText("\n".join(lines))

    def show_error(self, message: str) -> None:
        self.set_scanning(False)
        self.message.show_error(message)

    # Kept so the window's existing teardown/navigation hooks keep working.
    def reset(self) -> None:
        pass

    def shutdown(self) -> None:
        pass
