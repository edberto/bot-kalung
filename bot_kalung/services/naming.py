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


def container_size_short(size_raw: str | None) -> str:
    """PRD 8.3 — truncate at the first character after the foot mark.

    "40' HI-CUBE" -> "40'".  Falls back to the leading digits when the source
    has no foot mark ("40 HC" -> "40'").
    """
    if not size_raw:
        return ""
    text = size_raw.strip()
    mark = text.find("'")
    if mark != -1:
        return text[:mark + 1]
    digits = re.match(r"\s*(\d+)", text)
    return f"{digits.group(1)}'" if digits else text


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


def vessel_title(vessel_name: str | None) -> str:
    """Title case for the folder name — "EVER CONCERT" -> "Ever Concert".

    Confirmed with the user 2026-07-20: always the full name from the DO.
    """
    return " ".join(word.capitalize() for word in (vessel_name or "").split())


def folder_name(*, sequence: int, container_quantity: int, size_short: str,
                destination_port: str, booking_number: str, vessel_name: str,
                voyage: str, etd: date, sequence_width: int = 1) -> str:
    """PRD 9.3, used verbatim per the user's 2026-07-20 decision:

    {seq}.{qty}x{size_short}-{dest_lower}-{booking_no}-{vessel_title} {voyage}-{dd} {mmm_lower}

    `sequence_width` reproduces the zero-padding the exporter already uses on
    disk: TTJ numbers its folders "01." to "04.", THREESTAR uses "1.", AMJ "23.".
    Taking it from the previous folder keeps the new one consistent with its
    neighbours instead of imposing one exporter's habit on another.
    """
    return (
        f"{sequence:0{max(1, sequence_width)}d}.{container_quantity}x{size_short}"
        f"-{(destination_port or '').strip().lower()}"
        f"-{(booking_number or '').strip()}"
        f"-{vessel_title(vessel_name)} {(voyage or '').strip()}"
        f"-{etd.day:02d} {MONTHS_ABBR[etd.month - 1]}"
    )


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
