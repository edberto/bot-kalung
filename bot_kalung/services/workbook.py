"""Headless workbook reading (openpyxl) — the PC-free ingest path.

`excel.read_shipment_fields` reads a shipment's SI/VGM workbook through Excel COM
(xlwings), which needs Windows + installed Excel. This is the same read with no
Excel at all: openpyxl on the saved `.xlsx`, using each formula cell's
Excel-cached value (`data_only=True`). It was verified to match the COM reader
across every live shipment, so the folder scan can run on a plain Linux worker.

Only the cell access differs; the parsing (destination, ETD, party, vessel/
voyage, booking) is the exact same pure logic, reused from `excel`.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

from .excel import (
    ExcelError,
    ShipmentFields,
    _clean_booking,
    _normalize,
    _parse_long_date,
    _parse_party,
    _split_destination,
    _split_vessel_voyage,
    find_main_workbook,
)


# -- openpyxl cell lookup (mirrors excel.find_sheet / find_label / ...) -------

def _find_sheet(wb, prefix: str):
    """Match by normalized prefix so 'SI ', 'SI  ' and 'SI  benar' all resolve."""
    target = _normalize(prefix)
    for name in wb.sheetnames:
        if _normalize(name).startswith(target):
            return wb[name]
    return None


def _find_label(ws, text: str, *, column: int | None = None,
                exact: bool = False) -> tuple[int, int] | None:
    """Locate a label cell, case-insensitively. Returns (row, col) 1-based."""
    needle = text.strip().upper()
    for row in ws.iter_rows():
        for cell in row:
            value = cell.value
            if not isinstance(value, str):
                continue
            cell_text = value.strip().upper()
            if not cell_text:
                continue
            if column is not None and cell.column != column:
                continue
            if (cell_text == needle) if exact else (needle in cell_text):
                return cell.row, cell.column
    return None


def _value_after(ws, row: int, col: int) -> int:
    """First column right of a label, skipping the label's merged span (some
    templates merge the label across two columns, e.g. NIT's ETD in G:H)."""
    for rng in ws.merged_cells.ranges:
        if (rng.min_row <= row <= rng.max_row
                and rng.min_col <= col <= rng.max_col):
            return rng.max_col + 1
    return col + 1


def _cell(ws, row: int, col: int):
    return ws.cell(row=row, column=col).value


def _container_rows(ws) -> tuple[int, list[int]]:
    """(header_row, data_rows) for the VGM sheet's container table."""
    header = _find_label(ws, "CONTAINER NO")
    if header is None:
        raise ExcelError("Tidak menemukan tabel kontainer di sheet VGM.")
    header_row = header[0]
    rows: list[int] = []
    row = header_row + 1
    while True:
        index_value = _cell(ws, row, 2)
        if isinstance(index_value, (int, float)) and not isinstance(index_value, bool):
            rows.append(row)
            row += 1
            continue
        break
    return header_row, rows


# -- field reads (same logic as excel._read_si / _read_vgm) ------------------

def _read_si(ws, fields: ShipmentFields) -> None:
    for label in ("PORT OF DISCHARGE", "DESTINATION"):
        pod = _find_label(ws, label)
        if not pod:
            continue
        raw = _cell(ws, pod[0], _value_after(ws, *pod))
        port, country = _split_destination(raw)
        if port:
            fields.destination_port, fields.destination_country = port, country
            break

    etd = _find_label(ws, "ETD", exact=True)
    if etd:
        fields.etd = _parse_long_date(_cell(ws, etd[0], _value_after(ws, *etd)))

    party = _find_label(ws, "Party", exact=True)
    if party:
        raw = _cell(ws, party[0], _value_after(ws, *party))
        fields.container_quantity, fields.container_size_short = _parse_party(raw)


def _read_vgm(ws, fields: ShipmentFields) -> None:
    vessel = _find_label(ws, "VESSEL NAME", column=2)
    if vessel:
        fields.vessel_name, fields.voyage = _split_vessel_voyage(
            _cell(ws, vessel[0], 5))

    booking = _find_label(ws, "BOOKING NO", column=2)
    if booking:
        fields.booking_number = _clean_booking(_cell(ws, booking[0], 5))

    try:
        _, rows = _container_rows(ws)
    except ExcelError:
        fields.warnings.append("Tabel kontainer VGM tidak ditemukan.")
        return
    numbers = []
    for row in rows:
        value = _cell(ws, row, 4)      # column D — container number
        if isinstance(value, str) and value.strip():
            numbers.append(value.strip().upper())
    fields.containers = numbers
    if fields.container_quantity is None and rows:
        fields.container_quantity = len(rows)
    if fields.container_size_short is None and rows:
        size = _cell(ws, rows[0], 3)
        if isinstance(size, str) and size.strip():
            fields.container_size_short = size.strip()


def read_shipment_fields(folder) -> ShipmentFields:
    """Headless twin of `excel.read_shipment_fields`. Never raises — problems
    land in `warnings`."""
    fields = ShipmentFields()
    workbook = find_main_workbook(folder)
    if workbook is None or Path(workbook).name.startswith("~$"):
        fields.warnings.append("Tidak ada workbook VGM/SI/Inv/PL di folder ini.")
        return fields
    fields.workbook = workbook.name
    wb = None
    try:
        wb = openpyxl.load_workbook(workbook, data_only=True)
        si = _find_sheet(wb, "SI")
        if si is not None:
            _read_si(si, fields)
        else:
            fields.warnings.append("Sheet SI tidak ditemukan.")
        vgm = _find_sheet(wb, "VGM")
        if vgm is not None:
            _read_vgm(vgm, fields)
        else:
            fields.warnings.append("Sheet VGM tidak ditemukan.")
    except Exception as exc:      # noqa: BLE001 - a bad workbook must not crash a scan
        fields.warnings.append(f"Gagal membaca Excel: {exc}")
    finally:
        if wb is not None:
            wb.close()
    return fields
