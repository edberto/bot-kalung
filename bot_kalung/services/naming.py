"""Deterministic derivations from the extracted DO fields.

PRD Section 8.3 (post-processing) and Section 9.3 (folder naming). Nothing here
touches the LLM — these are pure transformations of already-extracted values.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

MONTHS_EN = [
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
]
MONTHS_ABBR = ["jan", "feb", "mar", "apr", "may", "jun",
               "jul", "aug", "sep", "oct", "nov", "dec"]


def is_quarantine_required(country: str | None, quarantine_countries) -> bool:
    """PRD 8.3 — destination country membership test, case-insensitive."""
    if not country:
        return False
    return country.strip().upper() in {c.strip().upper() for c in quarantine_countries}


def vgm_date_month(etd: date) -> str:
    """PRD 8.3 — roll to the following month when the ETD falls in the last
    three calendar days of its own month. Returned as "AUGUST 2026".
    """
    last_day = calendar.monthrange(etd.year, etd.month)[1]
    if etd.day > last_day - 3:
        month = etd.month % 12 + 1
        year = etd.year + (1 if etd.month == 12 else 0)
    else:
        month, year = etd.month, etd.year
    return f"{MONTHS_EN[month - 1]} {year}"


def document_number(sequence: int, etd: date) -> str:
    """PRD 8.3 — the shared SI/VGM number: [seq][MM][YYYY].

    The sequence is zero-padded to two digits, which the PRD does not say but
    every live workbook does: AMJ23 -> "23082026", TTJ04 -> "04072026",
    TSI01 -> "01072026". Without padding, a single-digit sequence produces
    "9092026", which is both wrong and ambiguous.

    Month and year come from the ETD on the DO, never from vgm_date_month and
    never from the month the documents happen to be prepared in. TSI01 was
    prepared in July for an August sailing and reads "01072026"; for that
    shipment this returns "01082026" on purpose (user decision, 2026-07-20).
    """
    return f"{sequence:02d}{etd.month:02d}{etd.year}"


def vgm_number(sequence: int, etd: date) -> str:
    return f"VGM-{document_number(sequence, etd)}"


def si_title(sequence: int, etd: date) -> str:
    return f"SHIPPING INSTRUCTION - {document_number(sequence, etd)}"


def etd_long(etd: date) -> str:
    """PRD 10.2 — the SI ETD cell, e.g. "03 AUGUST 2026"."""
    return f"{etd.day:02d} {MONTHS_EN[etd.month - 1]} {etd.year}"


def parse_iso_date(value: str | None) -> date | None:
    """Tolerant ISO parser for the LLM's etd_belawan field."""
    if not value:
        return None
    match = re.match(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})", str(value))
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
