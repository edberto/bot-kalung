"""Container list for a shipment (folder-scan tracker).

Shows each container read from the VGM with its latest BNCT status and which
terminal to look at. One shared "Buka di BNCT" button opens the portal and copies
the container numbers (the portal has no URL pre-fill, so the worker pastes each
number into the container search). Per container there are two shortcuts: copy
its number, and open its photo folder under the shipment's "Foto" directory.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from . import theme
from .widgets import CARD_STYLE, Panel, SecondaryButton, section_header

# The BNCT container search's terminal codes.
_TERMINAL = {"PTP": "PTP", "TPKB": "TPKB"}


def _foto_root(folder: Path | None) -> Path | None:
    """The shipment's "Foto" directory, matched case-insensitively."""
    if folder is None:
        return None
    try:
        if not folder.is_dir():
            return None
        for entry in sorted(folder.iterdir()):
            if entry.is_dir() and "foto" in entry.name.lower():
                return entry
    except OSError:
        return None
    return None


def photo_dirs(folder: Path | None) -> dict[str, Path]:
    """{UPPERCASE subfolder name: path} for the subfolders under "Foto"."""
    root = _foto_root(folder)
    if root is None:
        return {}
    dirs: dict[str, Path] = {}
    try:
        for entry in sorted(root.iterdir()):
            if entry.is_dir():
                dirs[entry.name.upper()] = entry
    except OSError:
        return {}
    return dirs


def match_photo_dir(container_no: str, dirs: dict[str, Path]) -> Path | None:
    """The photo subfolder whose name contains the container number."""
    key = (container_no or "").upper().strip()
    if not key:
        return None
    for name, path in dirs.items():
        if key in name:
            return path
    return None


class ContainersPanel(Panel):
    open_bnct = pyqtSignal()          # open the portal + copy the numbers
    copy_number = pyqtSignal(str)     # copy one container number
    open_photos = pyqtSignal(str)     # open a container's photo folder (path)

    def __init__(self):
        super().__init__()
        self.folder: Path | None = None
        self.setStyleSheet(theme.style(CARD_STYLE))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        top = QHBoxLayout()
        self.heading = section_header("Kontainer")
        top.addWidget(self.heading)
        top.addStretch(1)
        self.bnct_button = SecondaryButton("Buka di BNCT")
        self.bnct_button.setMinimumHeight(28)
        self.bnct_button.clicked.connect(self.open_bnct)
        top.addWidget(self.bnct_button)
        layout.addLayout(top)

        self.list_layout = QVBoxLayout()
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        layout.addLayout(self.list_layout)
        layout.addStretch(1)

    def apply(self, containers, folder=None):
        self.folder = Path(folder) if folder else None
        dirs = photo_dirs(self.folder)   # list the Foto folder once, not per card

        while self.list_layout.count():
            taken = self.list_layout.takeAt(0)
            if taken.widget() is not None:
                taken.widget().deleteLater()

        self.heading.setText(f"Kontainer ({len(containers)})")
        self.bnct_button.setEnabled(bool(containers))
        if not containers:
            empty = QLabel("Belum ada kontainer terbaca dari VGM.")
            empty.setStyleSheet(theme.style("font-size: 12px; color: #9ca3af;"))
            self.list_layout.addWidget(empty)
            return

        for container in containers:
            self.list_layout.addWidget(self._row(container, dirs))

    def _row(self, container, dirs) -> Panel:
        stack = container.at_stack_receiving
        accent = "#16a34a" if stack else "#cbd5e1"
        card = Panel()
        card.setStyleSheet(theme.style(
            "background: #f9fafb; border: 1px solid #e5e7eb;"
            f"border-left: 3px solid {accent}; border-radius: 8px;"))
        row = QVBoxLayout(card)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(2)

        title = container.container_no
        detail = " ".join(filter(None, [container.size, container.type]))
        if detail:
            title += f"  ·  {detail}"
        name = QLabel(title)
        name.setStyleSheet(theme.style(
            "border: none; font-size: 13px; font-weight: 600; color: #111827;"))
        row.addWidget(name)

        status = QLabel(container.status)
        status.setStyleSheet(theme.style(
            "border: none; font-size: 11px; font-weight: 600;"
            f"color: {'#16a34a' if stack else '#6b7280'};"))
        row.addWidget(status)

        # Which terminal to search on the portal.
        site = container.last_site
        where = (f"Cari di terminal {_TERMINAL.get(site, site)}" if site
                 else "Cari di terminal PTP & TPKB")
        terminal = QLabel(where)
        terminal.setStyleSheet(theme.style(
            "border: none; font-size: 11px; color: #6b7280;"))
        row.addWidget(terminal)

        # Per-container shortcuts: copy the number, and open its photo folder
        # (only enabled when a matching folder exists under "Foto").
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 6, 0, 0)
        actions.setSpacing(6)

        copy_btn = SecondaryButton("Salin Nomor")
        copy_btn.setMinimumHeight(26)
        copy_btn.clicked.connect(
            lambda: self.copy_number.emit(container.container_no))
        actions.addWidget(copy_btn)

        photo_dir = match_photo_dir(container.container_no, dirs)
        photo_btn = SecondaryButton("Buka Folder Foto")
        photo_btn.setMinimumHeight(26)
        photo_btn.setEnabled(photo_dir is not None)
        if photo_dir is not None:
            photo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            photo_btn.clicked.connect(
                lambda: self.open_photos.emit(str(photo_dir)))
        else:
            photo_btn.setToolTip(
                "Folder foto untuk kontainer ini tidak ditemukan.")
        actions.addWidget(photo_btn)
        actions.addStretch(1)
        row.addLayout(actions)
        return card
