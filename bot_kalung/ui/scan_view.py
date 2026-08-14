"""Placeholder for the folder-scan screen.

The shipment-creation wizard was removed; active shipments will instead be
discovered by scanning the Drive (built out in a later phase). This stub keeps
the view slot and the window's teardown hooks stable in the meantime.
"""

from __future__ import annotations

from . import theme

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class ScanView(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        title = QLabel("Pindai Folder Pengiriman")
        title.setStyleSheet(theme.style(
            "font-size: 22px; font-weight: 700; color: #111827;"))
        layout.addWidget(title)
        note = QLabel(
            "Pengiriman aktif akan ditemukan otomatis dengan memindai folder "
            "Google Drive. Fitur ini sedang disiapkan.")
        note.setWordWrap(True)
        note.setStyleSheet(theme.style("font-size: 13px; color: #6b7280;"))
        layout.addWidget(note)
        layout.addStretch(1)

    # Kept so the window's existing teardown/navigation hooks keep working.
    def reset(self) -> None:
        pass

    def shutdown(self) -> None:
        pass
