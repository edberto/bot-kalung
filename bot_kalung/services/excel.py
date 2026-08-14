"""Excel pre-fill via xlwings (PRD Section 10).

Written against the live AMJ workbook but deliberately anchor-based rather than
address-based, because the four exporters differ (see DECISIONS.md section 11):

* Sheet names vary ('SI ', 'SI  ', 'SI  benar') -> matched by normalized prefix.
* Label rows differ (AMJ's VGM block sits 5 rows lower) -> located by text.
* The SI ETD cell is in column B for AMJ but E/G elsewhere -> searched sheet-wide.
* Container rows in the SI are interleaved with HS-code text rows, so they are
  identified by their cross-reference formulas to the VGM sheet rather than by
  assuming a contiguous block.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import drive, naming


# Identifies a container row by its reference back to the VGM sheet, e.g.
# ='VGM '!D16 — present in the SI and P.List sheets.
VGM_REF_RE = re.compile(r"=\s*'?VGM\s*'?!\s*\$?([A-Z]+)\$?(\d+)", re.IGNORECASE)


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


def content_extent(sheet) -> tuple[int, int]:
    """Last row and column holding an actual value.

    Not `used_range`: AMJ's SI reports 122 rows but its real content stops at
    row 32, the rest being stray formatting. Used to bound the container-data
    column span.
    """
    used = sheet.used_range
    values = used.value
    if not isinstance(values, list):
        values = [[values]]
    if values and not isinstance(values[0], list):
        values = [[v] for v in values]

    last_row, last_col = used.row, used.column
    for r, row in enumerate(values):
        for c, value in enumerate(row):
            if value not in (None, ""):
                last_row = max(last_row, used.row + r)
                last_col = max(last_col, used.column + c)
    return last_row, last_col


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


# -- container row discovery ---------------------------------------------

def container_rows(sheet, vgm_data_rows: list[int],
                   ref_columns=("D", "E")) -> list[int]:
    """Rows that reference the VGM sheet's *container* block, in order.

    Must be narrow. The SI also references the VGM sheet for the booking number
    (`='VGM '!E11`), the feeder (`='VGM '!E12`) and its own title, so matching
    any VGM reference would classify those as container rows — and the row
    adjustment would then delete real content. A row only counts when it
    references one of the container columns at one of the VGM container rows.
    """
    targets = set(vgm_data_rows)
    used = sheet.used_range
    formulas = used.formula
    if not isinstance(formulas, (list, tuple)):
        formulas = [[formulas]]

    rows: list[int] = []
    for r, row in enumerate(formulas):
        if not isinstance(row, (list, tuple)):
            row = [row]
        for value in row:
            if not isinstance(value, str):
                continue
            match = VGM_REF_RE.search(value)
            if not match:
                continue
            column, referenced_row = match.group(1).upper(), int(match.group(2))
            if column in ref_columns and referenced_row in targets:
                rows.append(used.row + r)
                break
    return rows


def _duplicate_rows(sheet, source_row: int, count: int,
                    columns: tuple[int, int] | None = None):
    """Clone a container row downward `count` times, keeping format and formulas.

    `columns` restricts the copy to a (first, last) column span. That matters on
    the SI sheet, where a container row also carries HS-code narrative text in
    column B — copying the whole row would repeat that text for every extra
    container. Copying only the data span leaves the narrative alone.

    Uses a blank Insert followed by Copy(Destination) rather than the clipboard
    `Copy()` + `Insert()` idiom, which silently produced empty rows here.
    Relative references shift with the copy, so a cell referencing 'VGM'!D17
    becomes 'VGM'!D18 one row down.
    """
    for offset in range(count):
        source = source_row + offset
        target = source + 1
        sheet.api.Rows(target).Insert()
        if columns is None:
            sheet.api.Rows(source).Copy(sheet.api.Rows(target))
        else:
            first, last = columns
            src = sheet.range((source, first), (source, last))
            dst = sheet.range((target, first), (target, last))
            # Insert() inherits the merge layout of the row above; Excel refuses
            # to paste across merged cells, so clear them on the target first.
            dst.api.UnMerge()
            src.api.Copy(dst.api)
    sheet.book.app.api.CutCopyMode = False


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


# -- sheet pre-fill -------------------------------------------------------

def prefill_vgm(book, *, sequence: int, etd: date, booking_number: str,
                vessel_name: str, voyage: str, container_quantity: int,
                size_short: str, report: PrefillReport):
    """PRD 10.1."""
    sheet = find_sheet(book, "VGM")
    if sheet is None:
        raise ExcelError("Sheet VGM tidak ditemukan di file Excel.")

    # NO : VGM-<number> — the label and value share one cell in column B.
    # The SI title derives itself from this cell, so writing it updates both.
    no_cell = find_label(sheet, "NO :", column=2) or find_label(sheet, "VGM-", column=2)
    if no_cell:
        value = f"NO : {naming.vgm_number(sequence, etd)}"
        if _set(sheet, no_cell[0], no_cell[1], value, as_text=True, report=report):
            report.changed(f"VGM {sheet.range(no_cell).address}: {value}")
    else:
        report.warn("VGM: sel nomor VGM tidak ditemukan.")

    # DATE -> column E, red. Written as text: the cell's mmm-yy format would
    # otherwise turn "AUGUST 2026" into a datetime.
    date_cell = find_label(sheet, "DATE", column=2, exact=True)
    if date_cell:
        value = naming.vgm_date_month(etd)
        if _set(sheet, date_cell[0], 5, value, red=True, as_text=True, report=report):
            report.changed(f"VGM E{date_cell[0]}: {value} (merah)")
    else:
        report.warn("VGM: sel DATE tidak ditemukan.")

    # As text, or Excel drops the booking number's leading zero.
    booking_cell = find_label(sheet, "BOOKING NO", column=2)
    if booking_cell:
        value = str(booking_number).strip()
        if _set(sheet, booking_cell[0], 5, value, as_text=True, report=report):
            report.changed(f"VGM E{booking_cell[0]}: {value}")
    else:
        report.warn("VGM: sel BOOKING NO tidak ditemukan.")

    vessel_cell = find_label(sheet, "VESSEL NAME", column=2)
    if vessel_cell:
        value = f"{vessel_name} {voyage}".strip().upper()
        if _set(sheet, vessel_cell[0], 5, value, as_text=True, report=report):
            report.changed(f"VGM E{vessel_cell[0]}: {value}")
    else:
        report.warn("VGM: sel VESSEL NAME tidak ditemukan.")

    _adjust_vgm_containers(sheet, container_quantity, size_short, report)
    return sheet


def _adjust_vgm_containers(sheet, quantity: int, size_short: str,
                           report: PrefillReport):
    header_row, rows = vgm_container_rows(sheet)
    current = len(rows)

    if current == 0:
        report.warn("VGM: tidak ada baris kontainer untuk disesuaikan.")
        return

    if quantity > current:
        _duplicate_rows(sheet, rows[-1], quantity - current)
        report.changed(f"VGM: menambah {quantity - current} baris kontainer")
    elif quantity < current:
        for row in reversed(rows[quantity:]):
            sheet.range(f"{row}:{row}").api.Delete(-4162)  # xlUp
        report.changed(f"VGM: menghapus {current - quantity} baris kontainer")

    _, rows = vgm_container_rows(sheet)
    suffix = _size_suffix(sheet, rows)
    for index, row in enumerate(rows, start=1):
        _set(sheet, row, 2, index)
        _set(sheet, row, 3, f"{size_short}{suffix}")
        # PRD 10.1 — container no, seal no and tare stay blank for the worker.
        for col in (4, 5, 8):
            sheet.range((row, col)).value = None
    report.changed(
        f"VGM: {len(rows)} baris kontainer diisi ({size_short}{suffix})")


def _size_suffix(sheet, rows: list[int], default: str = "HQ") -> str:
    """The container-type suffix this workbook already uses.

    AMJ writes "40'HQ" and NIT writes "40'HC"; taking it from the sheet avoids
    imposing one exporter's wording on another.
    """
    for row in rows:
        value = sheet.range((row, 3)).value
        if not isinstance(value, str):
            continue
        match = re.search(r"([A-Za-z]+)\s*$", value.strip())
        if match:
            return match.group(1).upper()
    return default


def prefill_si(book, *, sequence: int, etd: date, container_quantity: int,
               vgm_data_rows: list[int], report: PrefillReport,
               size_short: str = ""):
    """PRD 10.2."""
    sheet = find_sheet(book, "SI")
    if sheet is None:
        raise ExcelError("Sheet SI tidak ditemukan di file Excel.")

    # In the AMJ template this cell is a formula deriving the number from the
    # VGM sheet, so _set skips it and the title updates on its own.
    title = find_label(sheet, "SHIPPING INSTRUCTION")
    if title:
        value = naming.si_title(sequence, etd)
        if _set(sheet, title[0], title[1], value, as_text=True, report=report):
            report.changed(f"SI {sheet.range(title).address}: {value}")
    else:
        report.warn("SI: judul SHIPPING INSTRUCTION tidak ditemukan.")

    # ETD sits in column B for AMJ but column E/G for the other exporters, so
    # search the whole sheet and write to the cell immediately to its right.
    etd_cell = find_label(sheet, "ETD", exact=True)
    if etd_cell:
        value = naming.etd_long(etd)
        target = value_column_after(sheet, etd_cell[0], etd_cell[1])
        if _set(sheet, etd_cell[0], target, value, red=True,
                as_text=True, report=report):
            report.changed(
                f"SI {sheet.range((etd_cell[0], target)).address}: "
                f"{value} (merah)")
    else:
        report.warn("SI: sel ETD tidak ditemukan.")

    # "Party" summarises the booking as e.g. "2 X 40'HC". A plain literal in the
    # template, so it goes stale on a quantity change unless rewritten. The
    # P.List sheet mirrors this cell by formula, so it follows automatically.
    if size_short:
        party = find_label(sheet, "Party", exact=True)
        if party:
            target = value_column_after(sheet, party[0], party[1])
            value = f"{container_quantity} X {size_short}HC"
            if _set(sheet, party[0], target, value, as_text=True, report=report):
                report.changed(
                    f"SI {sheet.range((party[0], target)).address}: {value}")
        else:
            report.warn("SI: sel Party tidak ditemukan.")

    _adjust_reference_rows(sheet, container_quantity, vgm_data_rows, "SI", report)
    return sheet


def prefill_plist(book, *, container_quantity: int, vgm_data_rows: list[int],
                  report: PrefillReport):
    """PRD 10.3."""
    sheet = find_sheet(book, "P.List")
    if sheet is None:
        report.warn("Sheet P.List tidak ditemukan; dilewati.")
        return None
    _adjust_reference_rows(sheet, container_quantity, vgm_data_rows,
                           "P.List", report)
    return sheet


def _data_columns(sheet) -> tuple[int, int] | None:
    """The (first, last) column span holding container data on a goods sheet.

    Starts at the BAGS column — everything left of it is the description or
    row-index area, which must not be duplicated. Ends at the sheet's last
    column holding content.
    """
    bags = find_label(sheet, "BAGS")
    if bags is None:
        return None
    _, last_col = content_extent(sheet)
    return bags[1], max(bags[1], last_col)


def _adjust_reference_rows(sheet, quantity: int, vgm_data_rows: list[int],
                           label: str, report: PrefillReport):
    """Grow or shrink the rows that cross-reference the VGM container block."""
    rows = container_rows(sheet, vgm_data_rows)
    if not rows:
        # Expected for some exporters: NIT's SI and P.List describe the cargo in
        # prose rather than one row per container, so there is nothing to grow
        # or shrink. Say so plainly instead of implying something went wrong.
        report.warn(
            f"{label}: tidak ada baris kontainer yang terhubung ke sheet VGM. "
            "Sesuaikan jumlah kontainer di sheet ini secara manual.")
        return
    current = len(rows)

    if quantity > current:
        _duplicate_rows(sheet, rows[-1], quantity - current,
                        columns=_data_columns(sheet))
        report.changed(f"{label}: menambah {quantity - current} baris kontainer")
    elif quantity < current:
        for row in reversed(rows[quantity:]):
            sheet.range(f"{row}:{row}").api.Delete(-4162)
        report.changed(f"{label}: menghapus {current - quantity} baris kontainer")
    else:
        report.changed(f"{label}: {current} baris kontainer sudah sesuai")


def prefill_workbook(book, *, sequence: int, etd: date, booking_number: str,
                     vessel_name: str, voyage: str, container_quantity: int,
                     size_short: str, report: PrefillReport | None = None
                     ) -> PrefillReport:
    """Run the whole PRD Section 10 pre-fill in a safe order.

    Sequencing matters because the SI and P.List cross-reference the VGM
    container rows. Growing goes VGM-first so the new rows exist before the
    dependants copy them; shrinking goes dependants-first, because deleting a
    VGM row turns the SI's `='VGM '!E17` into `#REF!`.
    """
    report = report or PrefillReport()

    vgm = find_sheet(book, "VGM")
    if vgm is None:
        raise ExcelError("Sheet VGM tidak ditemukan di file Excel.")
    _, original_rows = vgm_container_rows(vgm)
    shrinking = container_quantity < len(original_rows)

    if shrinking:
        prefill_si(book, sequence=sequence, etd=etd,
                   container_quantity=container_quantity,
                   vgm_data_rows=original_rows, report=report,
                   size_short=size_short)
        prefill_plist(book, container_quantity=container_quantity,
                      vgm_data_rows=original_rows, report=report)
        prefill_vgm(book, sequence=sequence, etd=etd, booking_number=booking_number,
                    vessel_name=vessel_name, voyage=voyage,
                    container_quantity=container_quantity, size_short=size_short,
                    report=report)
    else:
        prefill_vgm(book, sequence=sequence, etd=etd, booking_number=booking_number,
                    vessel_name=vessel_name, voyage=voyage,
                    container_quantity=container_quantity, size_short=size_short,
                    report=report)
        prefill_si(book, sequence=sequence, etd=etd,
                   container_quantity=container_quantity,
                   vgm_data_rows=original_rows, report=report,
                   size_short=size_short)
        prefill_plist(book, container_quantity=container_quantity,
                      vgm_data_rows=original_rows, report=report)
    return report



# -- opening a workbook without leaking an Excel process -------------------

@contextmanager
def open_book(path):
    """Yield the workbook at `path`, reusing an already-open copy if there is one.

    `xw.Book(path)` looks like "attach if open, else open", but when no Excel is
    running it *starts* one and leaves it running with the file locked. So the
    three cases are handled explicitly: reuse a book the user already has open
    (and leave it exactly as found), open into a running Excel (and close just
    the book), or start our own hidden Excel (and quit it).
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
            if len(xw.apps) > 0:
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


def find_main_workbook(folder) -> Path | None:
    """The VGM/SI/Inv/PL workbook in a shipment folder, or None."""
    try:
        entries = sorted(Path(folder).iterdir())
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
    """A date cell that may arrive as a real date or as "27 JANUARY 2026"."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", str(value).strip())
    if not match:
        return None
    try:
        month = naming.MONTHS_EN.index(match.group(2).upper()) + 1
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
        with open_book(workbook) as book:
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
