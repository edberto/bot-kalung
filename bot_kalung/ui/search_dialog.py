"""Async shipment search dialog — by container number or party size.

The query runs on a worker thread (the shared DB lives on a network drive, so a
lookup must never block the UI), and the matches come back as clickable cards
that open the shipment's detail page. Both search dimensions behave the same:
type, search off-thread, then pick a result to navigate.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget,
)

from . import theme

from ..core.constants import EXPORTER_COLORS
from ..services.search import search_shipments
from .widgets import Panel, PrimaryButton


class _SearchWorker(QThread):
    """Runs one search off the UI thread; `Database.query` opens its own
    connection, so it is safe to use here."""

    done = pyqtSignal(object, object)   # (results | None, error | None)

    def __init__(self, db, query: str, parent=None):
        super().__init__(parent)
        self._db = db
        self._query = query

    def run(self):
        try:
            results = search_shipments(self._db, self._query)
        except Exception as exc:  # noqa: BLE001 - a search must never crash the app
            self.done.emit(None, str(exc))
            return
        self.done.emit(results, None)


class _ResultCard(Panel):
    """A single match — clicking it opens that shipment."""

    clicked = pyqtSignal(str)

    def __init__(self, result):
        super().__init__()
        self._shipment_id = result.shipment_id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("searchResult")
        self.setStyleSheet(theme.style(
            "#searchResult { background: #ffffff; border: 1px solid #e5e7eb;"
            " border-radius: 8px; }"
            "#searchResult:hover { border-color: #93c5fd; background: #f8fafc; }"))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(6)
        color = EXPORTER_COLORS.get(result.code, "#6b7280")
        badge = QLabel(result.label)
        badge.setStyleSheet(theme.style(
            f"background: {color}; color: white; border-radius: 3px;"
            " padding: 1px 6px; font-size: 11px; font-weight: 700;"))
        top.addWidget(badge)
        match = QLabel(result.match)
        match.setStyleSheet(theme.style("font-size: 11px; color: #2563eb;"))
        top.addWidget(match)
        top.addStretch(1)
        outer.addLayout(top)

        subtitle = QLabel(result.subtitle)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(theme.style("font-size: 12px; color: #374151;"))
        outer.addWidget(subtitle)

    def mousePressEvent(self, event):
        self.clicked.emit(self._shipment_id)


class SearchDialog(QDialog):
    """Search active shipments and jump to one's detail page."""

    shipment_opened = pyqtSignal(str)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._worker: _SearchWorker | None = None

        self.setWindowTitle("Cari Pengiriman")
        self.setMinimumWidth(480)
        self.setStyleSheet(theme.style("QDialog { background: #ffffff; }"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        heading = QLabel("Cari Pengiriman")
        heading.setStyleSheet(theme.style(
            "font-size: 17px; font-weight: 800; color: #0f172a;"))
        layout.addWidget(heading)

        hint = QLabel("Berdasarkan nomor kontainer atau party size "
                      "(mis. \"CMAU\", \"10\", \"40'\").")
        hint.setWordWrap(True)
        hint.setStyleSheet(theme.style("font-size: 12px; color: #6b7280;"))
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setMinimumHeight(36)
        self.input.setPlaceholderText("Nomor kontainer atau party size…")
        self.input.setStyleSheet(theme.style(
            "QLineEdit { border: 1px solid #d1d5db; border-radius: 6px;"
            " padding: 6px 10px; font-size: 13px; }"
            "QLineEdit:focus { border-color: #2563eb; }"))
        self.input.returnPressed.connect(self._start_search)
        row.addWidget(self.input, 1)
        self.search_button = PrimaryButton("Cari")
        self.search_button.clicked.connect(self._start_search)
        row.addWidget(self.search_button)
        layout.addLayout(row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(theme.style("font-size: 12px; color: #6b7280;"))
        layout.addWidget(self.status)

        # Scrollable results — a query can match several shipments.
        self.results_host = QWidget()
        self.results_layout = QVBoxLayout(self.results_host)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(6)
        self.results_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.results_host)
        scroll.setMinimumHeight(220)
        layout.addWidget(scroll, 1)

        self.input.setFocus()

    # -- search ------------------------------------------------------------

    def _start_search(self):
        query = self.input.text().strip()
        if not query:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._clear_results()
        self.search_button.setEnabled(False)
        self.status.setText("Mencari…")
        self._worker = _SearchWorker(self._db, query, self)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, results, error):
        self.search_button.setEnabled(True)
        if error is not None:
            self.status.setText(f"Kesalahan saat mencari: {error}")
            return
        if not results:
            self.status.setText("Tidak ada pengiriman aktif yang cocok.")
            return
        self.status.setText(
            f"{len(results)} pengiriman ditemukan — klik untuk membuka.")
        for result in results:
            card = _ResultCard(result)
            card.clicked.connect(self._open)
            self.results_layout.insertWidget(
                self.results_layout.count() - 1, card)

    def _open(self, shipment_id: str):
        self.shipment_opened.emit(shipment_id)
        self.accept()

    def _clear_results(self):
        while self.results_layout.count() > 1:   # keep the trailing stretch
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def reject(self):
        # Don't tear down the dialog while a search thread is still running.
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(5000)
        super().reject()
