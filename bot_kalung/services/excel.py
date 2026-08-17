"""Excel access via xlwings (COM): shared cell helpers + the folder-scan reader.

Deliberately anchor-based rather than address-based, because the exporters'
templates differ (see DECISIONS.md section 11):

* Sheet names vary ('SI ', 'SI  ', 'SI  benar') -> matched by normalized prefix.
* Label rows differ (AMJ's VGM block sits 5 rows lower) -> located by text.
* The SI ETD cell is in column B for AMJ but E/G elsewhere -> searched sheet-wide.

Holds the cell helpers (find_sheet / find_label / value_column_after / _set),
the workbook opener (open_book), and read_shipment_fields. The resequence and
ETD-change writers reuse the helpers; the headless openpyxl reader in
`services/workbook.py` mirrors the reads for the PC-free ingest path.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import drive, naming


class ExcelError(Exception):
    """Carries an Indonesian, user-presentable message."""


@dataclass
class PrefillReport:
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skips: list[str] = field(default_factory=list)

    def changed(self, message: str):
        self.changes.append(message)

    def warn(self, message: str):
        self.warnings.append(message)

    def skipped(self, message: str):
        """A cell deliberately left alone (formula-backed)."""
        self.skips.append(message)


# -- sheet and cell lookup ------------------------------------------------

def _normalize(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().upper()


def find_sheet(book, prefix: str):
    """Match by normalized prefix so 'SI ', 'SI  ' and 'SI  benar' all resolve."""
    target = _normalize(prefix)
    for sheet in book.sheets:
        if _normalize(sheet.name).startswith(target):
            return sheet
    return None


def find_label(sheet, text: str, *, column: int | None = None,
               exact: bool = False) -> tuple[int, int] | None:
    """Locate a label cell, case-insensitively. Returns (row, col) or None."""
    needle = text.strip().upper()
    used = sheet.used_range
    values = used.value
    if not isinstance(values, list):
        values = [[values]]
    if values and not isinstance(values[0], list):
        values = [[v] for v in values]

    for r, row in enumerate(values):
        for c, value in enumerate(row):
            if not isinstance(value, str):
                continue
            cell_text = value.strip().upper()
            if not cell_text:
                continue
            row_index = used.row + r
            col_index = used.column + c
            if column is not None and col_index != column:
                continue
            if (cell_text == needle) if exact else (needle in cell_text):
                return row_index, col_index
    return None


def value_column_after(sheet, row: int, col: int) -> int:
    """First column to the right of a label, skipping the label's merged span.

    NIT merges its SI labels across two columns — `ETD` occupies G20:H20 and its
    value lives in I20 — so writing to `col + 1` would land inside the label.
    """
    try:
        area = sheet.range((row, col)).api.MergeArea
        last = area.Column + area.Columns.Count - 1
        return int(last) + 1
    except Exception:
        return col + 1


def _set(sheet, row: int, col: int, value, *, red: bool = False,
         as_text: bool = False, report: PrefillReport | None = None) -> bool:
    """Write a cell, refusing to clobber a formula.

    Several template cells derive themselves — the SI title is
    `=CONCATENATE("SHIPPING INSTRUCTION - ", RIGHT('VGM '!$B$8,8))`, so writing
    a literal there would break the link to the VGM number. Returns False when
    the write was skipped.
    """
    cell = sheet.range((row, col))
    existing = cell.formula
    if isinstance(existing, str) and existing.startswith("="):
        if report is not None:
            report.skipped(f"{sheet.name.strip()} {cell.address}: "
                           f"dilewati, berisi rumus ({existing[:40]})")
        return False

    # Excel coerces strings to match the cell's number format: a date format
    # turns "AUGUST 2026" into a datetime, and General strips the leading zero
    # from a booking number. Force text first.
    if as_text:
        cell.number_format = "@"
    cell.value = value
    if red:
        cell.api.Font.Color = 255  # BGR for pure red
    return True


# -- VGM container table --------------------------------------------------

def vgm_container_rows(sheet) -> tuple[int, list[int]]:
    """(header_row, data_rows) for the VGM sheet's container table."""
    header = find_label(sheet, "CONTAINER NO")
    if header is None:
        raise ExcelError("Tidak menemukan tabel kontainer di sheet VGM.")
    header_row = header[0]

    rows: list[int] = []
    row = header_row + 1
    while True:
        index_value = sheet.range((row, 2)).value
        if isinstance(index_value, (int, float)):
            rows.append(row)
            row += 1
            continue
        break
    return header_row, rows


# -- opening a workbook without leaking an Excel process -------------------

@contextmanager
def open_book(path, *, hidden: bool = False):
    """Yield the workbook at `path`, reusing an already-open copy if there is one.

    `xw.Book(path)` looks like "attach if open, else open", but when no Excel is
    running it *starts* one and leaves it running with the file locked. So the
    cases are handled explicitly: reuse a book the user already has open (and
    leave it exactly as found), open into a running Excel (and close just the
    book), or start our own hidden Excel (and quit it).

    `hidden=True` (used by the folder scan, which reads many workbooks in the
    background) never opens into the user's visible Excel — it always spins a
    dedicated invisible instance, so scanning does not pop a pile of workbook
    windows in front of whatever the user is doing.
    """
    import xlwings as xw

    target = Path(path).resolve()
    book = app = None
    close_book = False

    for running in xw.apps:
        for candidate in running.books:
            try:
                if Path(candidate.fullname).resolve() == target:
                    book = candidate
                    break
            except Exception:      # an unsaved book has no fullname
                continue
        if book is not None:
            break

    try:
        if book is None:
            if not hidden and len(xw.apps) > 0:
                book = xw.apps.active.books.open(str(target), update_links=False)
                close_book = True
            else:
                app = xw.App(visible=False, add_book=False)
                app.display_alerts = False
                book = app.books.open(str(target), update_links=False)
                close_book = True
        yield book
    finally:
        if close_book and book is not None:
            try:
                book.close()
            except Exception:
                pass
        if app is not None:
            try:
                app.quit()
            except Exception:
                pass


# -- reading a shipment's fields (folder-scan tracker) --------------------
#
# The tracker imports shipments straight off the Drive, so it reads what a
# shipment already knows from its own SI/VGM workbook rather than from a DO. The
# label positions are the same ones prefill_* writes to (confirmed against the
# live NIT01 workbook, 2026-08):
#   SI  — "Port of Discharge" -> "KARACHI, PAKISTAN", "ETD" -> "27 JANUARY 2026",
#         "Party" -> "5 X 40'HC".
#   VGM — "VESSEL NAME" (col E) -> "INTEGRA-162E", "BOOKING NO" (col E), and the
#         container table under "CONTAINER NO" (col D holds the numbers).
# The VGM's own "DATE" cell is unreliable (it read 2025 for a 2026 ETD), so the
# ETD is always taken from the SI.


@dataclass
class ShipmentFields:
    """What the scanner can read off a shipment's main workbook. Everything is
    optional — a partially-filled workbook still imports with what it has.
    """
    destination_port: str | None = None
    destination_country: str | None = None
    etd: date | None = None
    vessel_name: str | None = None
    voyage: str | None = None
    booking_number: str | None = None
    container_quantity: int | None = None
    container_size_short: str | None = None
    containers: list[str] = field(default_factory=list)  # container numbers
    workbook: str | None = None
    warnings: list[str] = field(default_factory=list)


def find_main_workbook(folder):
    """The VGM/SI/Inv/PL workbook in a shipment folder, or None.

    `folder` is a filesystem path (str/Path) or a Path-like Drive node — anything
    exposing `iterdir()` with `.name`/`.is_file()` entries — so the same lookup
    serves the local scan and the Drive-API scan.
    """
    node = folder if hasattr(folder, "iterdir") else Path(folder)
    try:
        entries = sorted(node.iterdir())
    except OSError:
        return None
    for entry in entries:
        if entry.is_file() and drive.is_main_workbook(entry.name):
            return entry
    return None


def _split_destination(value) -> tuple[str | None, str | None]:
    """"KARACHI, PAKISTAN" -> ("Karachi", "Pakistan")."""
    if not isinstance(value, str) or not value.strip():
        return None, None
    parts = [p.strip() for p in value.split(",", 1)]
    port = parts[0].title() or None
    country = parts[1].title() if len(parts) == 2 and parts[1] else None
    return port, country


def _parse_long_date(value) -> date | None:
    """A date cell that may arrive as a real date or as text — full or
    abbreviated month, e.g. "27 JANUARY 2026", "15 AUG 2026", "1 Sept 2026"."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", str(value).strip())
    if not match:
        return None
    # Match on the month's first three letters, so an abbreviation ("AUG",
    # "Sept") resolves the same as the full name ("AUGUST", "SEPTEMBER").
    token = match.group(2).upper()[:3]
    months = [m[:3] for m in naming.MONTHS_EN]
    try:
        month = months.index(token) + 1
        return date(int(match.group(3)), month, int(match.group(1)))
    except (ValueError, IndexError):
        return None


def _parse_party(value) -> tuple[int | None, str | None]:
    """"5 X 40'HC" -> (5, "40'HC")."""
    if not isinstance(value, str):
        return None, None
    match = re.match(r"\s*(\d+)\s*[Xx]\s*(.+)", value)
    if not match:
        return None, None
    return int(match.group(1)), match.group(2).strip() or None


def _split_vessel_voyage(value) -> tuple[str | None, str | None]:
    """Split the VGM's combined "VESSEL NAME" cell into (vessel, voyage).

    The voyage is the trailing token after the last space/hyphen, but only when
    it carries both a letter and a digit — that tells a real voyage ("162E",
    "021N", "N375") apart from a number that is part of the vessel's own name
    ("WAN HAI 101"). Formats seen live: "INTEGRA-162E", "WAN HAI 101-N375",
    "MAO GANG GUANG ZHOU 021N".
    """
    if not isinstance(value, str) or not value.strip():
        return None, None
    text = value.strip()
    match = re.match(r"^(?P<vessel>.*\S)[\s\-]+(?P<voyage>\S+)$", text)
    if match:
        voyage = match.group("voyage")
        if re.search(r"[A-Za-z]", voyage) and re.search(r"\d", voyage):
            return match.group("vessel").strip(), voyage.upper()
    return text, None


def _clean_booking(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def _read_si(sheet, fields: ShipmentFields) -> None:
    # Most exporters label it "Port of Discharge"; AMJ uses "Destination".
    for label in ("PORT OF DISCHARGE", "DESTINATION"):
        pod = find_label(sheet, label)
        if not pod:
            continue
        raw = sheet.range((pod[0], value_column_after(sheet, *pod))).value
        port, country = _split_destination(raw)
        if port:
            fields.destination_port, fields.destination_country = port, country
            break

    etd = find_label(sheet, "ETD", exact=True)
    if etd:
        raw = sheet.range((etd[0], value_column_after(sheet, *etd))).value
        fields.etd = _parse_long_date(raw)

    party = find_label(sheet, "Party", exact=True)
    if party:
        raw = sheet.range((party[0], value_column_after(sheet, *party))).value
        fields.container_quantity, fields.container_size_short = _parse_party(raw)


def _read_vgm(sheet, fields: ShipmentFields) -> None:
    # VGM label sits in column B; its value is in column E across every exporter
    # (the same cell prefill writes to).
    vessel = find_label(sheet, "VESSEL NAME", column=2)
    if vessel:
        fields.vessel_name, fields.voyage = _split_vessel_voyage(
            sheet.range((vessel[0], 5)).value)

    booking = find_label(sheet, "BOOKING NO", column=2)
    if booking:
        fields.booking_number = _clean_booking(sheet.range((booking[0], 5)).value)

    try:
        _, rows = vgm_container_rows(sheet)
    except ExcelError:
        fields.warnings.append("Tabel kontainer VGM tidak ditemukan.")
        return
    numbers = []
    for row in rows:
        value = sheet.range((row, 4)).value      # column D — container number
        if isinstance(value, str) and value.strip():
            numbers.append(value.strip().upper())
    fields.containers = numbers
    # The container-row count is the party size; trust it when the SI had no
    # "Party" cell, and take the size from the first row likewise.
    if fields.container_quantity is None and rows:
        fields.container_quantity = len(rows)
    if fields.container_size_short is None and rows:
        size = sheet.range((rows[0], 3)).value
        if isinstance(size, str) and size.strip():
            fields.container_size_short = size.strip()


def read_shipment_fields(folder) -> ShipmentFields:
    """Read destination, ETD, vessel/voyage, party size and containers from a
    shipment folder's main workbook. Never raises — problems land in `warnings`.
    """
    fields = ShipmentFields()
    workbook = find_main_workbook(folder)
    if workbook is None:
        fields.warnings.append("Tidak ada workbook VGM/SI/Inv/PL di folder ini.")
        return fields
    fields.workbook = workbook.name
    try:
        # hidden=True: the scan reads in the background and must not pop workbook
        # windows into the user's Excel.
        with open_book(workbook, hidden=True) as book:
            si = find_sheet(book, "SI")
            if si is not None:
                _read_si(si, fields)
            else:
                fields.warnings.append("Sheet SI tidak ditemukan.")
            vgm = find_sheet(book, "VGM")
            if vgm is not None:
                _read_vgm(vgm, fields)
            else:
                fields.warnings.append("Sheet VGM tidak ditemukan.")
    except Exception as exc:      # noqa: BLE001 - a bad workbook must not crash a scan
        fields.warnings.append(f"Gagal membaca Excel: {exc}")
    return fields
