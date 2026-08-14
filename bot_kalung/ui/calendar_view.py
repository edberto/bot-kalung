"""Month calendar for the dashboard (2026-07-22).

Shows every dated step and every shipment ETD on its day. Colour carries the
state, in this priority order:

    done      -> green    (completion beats overdue)
    overdue   -> red      (not done and strictly before today)
    pending   -> blue     (not done, today or later)
    ETD       -> amber    (a fourth accent, so sailing dates never read as steps)

The colours come from `theme.banner()`, which already returns a themed
(text, background, border) triple, so the calendar follows light/dark for free.

The widget owns the displayed month and asks for data by emitting
`month_changed(start_iso, end_iso)`; it never touches the database itself.
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date

from . import theme

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QStyle,
    QVBoxLayout, QWidget,
)

from .widgets import MONTHS_ID, Panel, SecondaryButton

DAY_HEADERS = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]

# Legend: ETD colour by where the date came from.
ETD_LEGEND = [("ETD dari BNCT (voyage)", "info"), ("ETD dari SI", "warning")]


def _kind_for(entry, today_iso: str) -> str:
    if entry.kind == "etd":
        # BNCT (or a manual voyage-wide set) is the authoritative voyage ETD;
        # a plain SI ETD is per-shipment and may not agree across the voyage.
        return ("etd_bnct" if entry.etd_source in ("bnct", "manual")
                else "etd_si")
    if entry.is_complete:
        return "done"
    return "overdue" if entry.is_overdue(today_iso) else "pending"


def _palette(state: str) -> tuple[str, str, str]:
    """(text, background, border) for an entry card."""
    return theme.banner({
        "done": "success", "overdue": "error", "pending": "info",
        "etd_bnct": "info", "etd_si": "warning", "etd": "warning",
    }[state])


class EntryCard(Panel):
    """One clickable item inside a day cell. The title wraps rather than crops."""

    clicked = pyqtSignal(str, str)      # shipment_id, step_code ("" for ETD)

    def __init__(self, entry, state: str):
        super().__init__()
        self._entry = entry
        fg, bg, border = _palette(state)
        self.state = state
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Minimum)
        self.setStyleSheet(theme.style(
            f"background: {bg}; border: 1px solid {border};"
            "border-radius: 4px;"))
        self.setToolTip(f"{entry.label} · {entry.title}")

        row = QHBoxLayout(self)
        row.setContentsMargins(5, 3, 5, 3)
        self.label = QLabel(f"{entry.label} · {entry.title}")
        self.label.setWordWrap(True)      # wrap long titles instead of cropping
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setStyleSheet(
            f"border: none; background: transparent; color: {fg};"
            "font-size: 10px; font-weight: 600;")
        row.addWidget(self.label)

    def mousePressEvent(self, event):
        self.clicked.emit(self._entry.shipment_id, self._entry.step_code or "")


class DayCell(Panel):
    """One day in the grid: its number plus a scrollable list of entry cards."""

    entry_clicked = pyqtSignal(str, str)

    def __init__(self, day: int | None, entries, *, is_today: bool,
                 today_iso: str):
        super().__init__()
        self.day = day
        self.is_today = is_today
        self.setMinimumHeight(84)
        # Never let contents dictate the cell's width — the seven columns share
        # the calendar evenly; entries wrap and scroll within the cell instead.
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)

        if day is None:            # padding cell from the previous/next month
            self.setStyleSheet(theme.style(
                "background: #fafafa; border: 1px solid #e5e7eb;"
                "border-radius: 6px;"))
            return

        if is_today:
            self.setStyleSheet(theme.style(
                "background: #eef2ff; border: 2px solid #2563eb;"
                "border-radius: 6px;"))
        else:
            self.setStyleSheet(theme.style(
                "background: white; border: 1px solid #e5e7eb;"
                "border-radius: 6px;"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(3)

        number = QLabel(str(day))
        number.setStyleSheet(theme.style(
            "border: none; background: transparent; font-size: 11px;"
            + ("font-weight: 700; color: #2563eb;" if is_today
               else "color: #6b7280;")))
        layout.addWidget(number)

        # A vertical scroll area so a busy day keeps every entry (wrapped) instead
        # of hiding the overflow; the horizontal bar is off so nothing spills out.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("background: transparent; border: none;")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        entries_layout = QVBoxLayout(container)
        entries_layout.setContentsMargins(0, 0, 0, 0)
        entries_layout.setSpacing(3)
        self.cards: list[EntryCard] = []
        for entry in entries:
            card = EntryCard(entry, _kind_for(entry, today_iso))
            card.clicked.connect(self.entry_clicked)
            entries_layout.addWidget(card)
            self.cards.append(card)
        entries_layout.addStretch(1)
        self.scroll.setWidget(container)
        layout.addWidget(self.scroll, 1)


class MonthCalendar(QWidget):
    """A month grid the dashboard fills with entries."""

    entry_clicked = pyqtSignal(str, str)     # shipment_id, step_code
    month_changed = pyqtSignal(str, str)     # start_iso, end_iso

    def __init__(self):
        super().__init__()
        today = date.today()
        self.year, self.month = today.year, today.month
        self._entries = []
        self.cells: dict[int, DayCell] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # Qt draws these arrows itself, so they never depend on a font having
        # the ◀/▶ glyphs (which it may not). The month label is a fixed width
        # and centred, so the arrows stay put whatever the month is called —
        # otherwise "September" pushes the next arrow further than "Juli".
        header = QHBoxLayout()
        header.setSpacing(8)
        style = self.style()

        self.prev_button = SecondaryButton("")
        self.prev_button.setIcon(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowLeft))
        self.prev_button.setFixedSize(34, 30)
        self.prev_button.setToolTip("Bulan sebelumnya")
        self.prev_button.clicked.connect(lambda: self.shift(-1))
        header.addWidget(self.prev_button)

        self.title = QLabel()
        self.title.setFixedWidth(170)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet(theme.style(
            "font-size: 15px; font-weight: 700; color: #111827;"))
        header.addWidget(self.title)

        self.next_button = SecondaryButton("")
        self.next_button.setIcon(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowRight))
        self.next_button.setFixedSize(34, 30)
        self.next_button.setToolTip("Bulan berikutnya")
        self.next_button.clicked.connect(lambda: self.shift(1))
        header.addWidget(self.next_button)
        header.addStretch(1)

        self.today_button = SecondaryButton("Hari ini")
        self.today_button.setFixedHeight(30)
        self.today_button.clicked.connect(self.go_to_today)
        header.addWidget(self.today_button)
        outer.addLayout(header)

        # Legend so the ETD colours are self-explanatory.
        legend = QHBoxLayout()
        legend.setSpacing(6)
        legend.addStretch(1)
        for text, kind in ETD_LEGEND:
            _, _, border = theme.banner(kind)
            dot = QLabel("●")
            dot.setStyleSheet(theme.style(f"color: {border}; font-size: 12px;"))
            label = QLabel(text)
            label.setStyleSheet(theme.style("font-size: 10px; color: #6b7280;"))
            legend.addWidget(dot)
            legend.addWidget(label)
            legend.addSpacing(10)
        outer.addLayout(legend)

        self.grid = QGridLayout()
        self.grid.setSpacing(4)
        # Seven equal columns, so no day can grow wider than its neighbours.
        for column in range(7):
            self.grid.setColumnStretch(column, 1)
            self.grid.setColumnMinimumWidth(column, 0)
        for column, name in enumerate(DAY_HEADERS):
            label = QLabel(name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(theme.style(
                "font-size: 10px; font-weight: 700; color: #9ca3af;"))
            self.grid.addWidget(label, 0, column)
        outer.addLayout(self.grid)

        self._render()

    # -- navigation --------------------------------------------------------

    def shift(self, months: int):
        month = self.month + months
        year = self.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        self.year, self.month = year, month
        self._announce()

    def go_to_today(self):
        today = date.today()
        self.year, self.month = today.year, today.month
        self._announce()

    def showing_today(self) -> bool:
        today = date.today()
        return (self.year, self.month) == (today.year, today.month)

    def range_iso(self) -> tuple[str, str]:
        last = _calendar.monthrange(self.year, self.month)[1]
        return (date(self.year, self.month, 1).isoformat(),
                date(self.year, self.month, last).isoformat())

    def _announce(self):
        start, end = self.range_iso()
        self.month_changed.emit(start, end)

    def reload(self):
        """Ask the owner for this month's entries again."""
        self._announce()

    # -- data --------------------------------------------------------------

    def set_entries(self, entries):
        self._entries = list(entries)
        self._render()

    def _render(self):
        self.title.setText(f"{MONTHS_ID[self.month - 1]} {self.year}")
        today = date.today()
        today_iso = today.isoformat()

        by_day: dict[int, list] = {}
        for entry in self._entries:
            try:
                day = date.fromisoformat(entry.date[:10]).day
            except ValueError:
                continue
            by_day.setdefault(day, []).append(entry)
        # ETDs after steps, so the day's work reads first.
        for items in by_day.values():
            items.sort(key=lambda e: (e.kind == "etd", e.label))

        # Clear previous day cells (row 0 holds the weekday headers).
        while self.grid.count() > len(DAY_HEADERS):
            item = self.grid.takeAt(self.grid.count() - 1)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.cells.clear()

        weeks = _calendar.monthcalendar(self.year, self.month)
        for row, week in enumerate(weeks, start=1):
            for column, day in enumerate(week):
                is_today = (day != 0 and self.showing_today()
                            and day == today.day)
                cell = DayCell(day or None, by_day.get(day, []),
                               is_today=is_today, today_iso=today_iso)
                cell.entry_clicked.connect(self.entry_clicked)
                self.grid.addWidget(cell, row, column)
                if day:
                    self.cells[day] = cell
        # Week rows share the vertical space equally, so every cell is the same
        # height and a busy day scrolls its entries rather than growing the row.
        for row in range(1, 7):
            self.grid.setRowStretch(row, 1 if row <= len(weeks) else 0)
