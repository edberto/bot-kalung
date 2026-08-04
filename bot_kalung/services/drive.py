"""Google Drive folder resolution, shipment scanning, and filename derivation.

Deviates from the PRD in ways confirmed with the user on 2026-07-20, all driven
by the live Drive layout:

1. PRD 9.1 assumes shipment folders sit at `{root}/{exporter}/`. Three layouts
   actually exist: `{exporter}/{year}/` (AMJ, NIT), `{exporter}/{year} Tasha/`
   (TTJ), and `{exporter}/` directly (INDO, THREESTAR). Driven by
   DEFAULT_SHIPMENT_SUBPATH.
2. PRD 9.2 renames documents to a fixed `{EXPORTER}{seq}-VGM,SI,Inv,PL.xls`.
   Real conventions vary per exporter in extension, embedded destination, code,
   and zero-padding, so the new names are derived from the source folder's own
   files instead of being hardcoded.
3. A folder's numeric prefix is not trustworthy: `40.1x40-Faizal` contains
   `AMJ04-VGM,SI,Inv,PL.xls`, i.e. it is really shipment 4. The sequence is
   therefore read from the document filename where one exists.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.constants import (
    DEFAULT_EXPORTER_FOLDERS,
    DEFAULT_FILE_CODES,
    DEFAULT_SHIPMENT_SUBPATH,
    EXPORTERS,
)

SEQ_PREFIX_RE = re.compile(r"^(\d+)\.")

# The main workbook combines the VGM, SI, Invoice and Packing List sheets, but
# its filename varies wildly across exporters: comma- or space-separated tokens,
# "PL" or "P.List", .xls or .xlsx, the code before or after the tokens, extra
# dots. Rather than chase one shape, it is identified by the presence of all four
# tokens. Every earlier regex was too strict and quietly failed to find (or,
# worse, deleted) a workbook whose name did not fit.
_EXCEL_RE = re.compile(r"\.xlsx?$", re.IGNORECASE)
_PL_RE = re.compile(r"P\.?\s*LIST|PL", re.IGNORECASE)   # PL, P.List, P List…
_COPY_RE = re.compile(r"\(\s*\d+\s*\)")                 # a " (1)" duplicate copy
# The exporter code + sequence prefix, when the name has one (AMJ23, GGN-00004).
_CODE_SEQ_RE = re.compile(r"^(?P<code>[A-Za-z]+)-?(?P<seq>\d+)")


def is_main_workbook(name: str) -> bool:
    """The VGM/SI/Inv/PL workbook, in any exporter's naming.

    All four tokens must be present; a bare `.pdf` or a `" (1)"` duplicate copy
    is rejected so the real file wins.
    """
    if not _EXCEL_RE.search(name) or _COPY_RE.search(name):
        return False
    upper = name.upper()
    return ("VGM" in upper and "SI" in upper and "INV" in upper
            and _PL_RE.search(name) is not None)


def main_workbook_sequence(name: str) -> int | None:
    """The sequence from a `{code}{seq}` prefix, or None when the name has none
    (then the caller falls back to the folder's numeric prefix)."""
    match = _CODE_SEQ_RE.match(name)
    return int(match.group("seq")) if match else None


def is_invoice(name: str) -> bool:
    """The billing workbook (`Invoice tagihan …` or `Inv-…`), never the main one."""
    if is_main_workbook(name) or not _EXCEL_RE.search(name):
        return False
    return bool(re.search(r"inv", name, re.IGNORECASE) and INVOICE_RE.match(name))

# Matches the billing workbook in either convention:
#   Invoice tagihan AMJ23.xlsx     Inv-TTJ04.xlsx
INVOICE_RE = re.compile(
    r"^(?P<prefix>.*?)(?P<code>[A-Za-z]+)(?P<seq>\d+)(?P<ext>\.xlsx?)$",
    re.IGNORECASE,
)


# -- exporter folder resolution -----------------------------------------

def exporter_folder_map(settings) -> dict[str, str]:
    configured = settings.get("exporter_folders") or {}
    return {code: configured.get(code, DEFAULT_EXPORTER_FOLDERS.get(code, code))
            for code in EXPORTERS}


def resolve_exporter_folder(drive_root, code: str, settings) -> Path | None:
    folder = exporter_folder_map(settings).get(code, code)
    path = Path(drive_root) / folder
    return path if path.is_dir() else None


def validate_drive_root(root, settings=None) -> tuple[bool, list[str]]:
    """PRD Section 3 Step 1 — at least one exporter folder must be present."""
    path = Path(root)
    if not path.is_dir():
        return False, []
    mapping = (exporter_folder_map(settings) if settings is not None
               else dict(DEFAULT_EXPORTER_FOLDERS))
    found = [c for c, folder in mapping.items() if (path / folder).is_dir()]
    return bool(found), found


def shipment_dir(exporter_folder, code: str, year: int, settings=None) -> Path:
    """The directory holding this exporter's shipment folders for a year."""
    configured = (settings.get("shipment_subpaths") or {}) if settings else {}
    template = configured.get(code, DEFAULT_SHIPMENT_SUBPATH.get(code, "{year}"))
    subpath = template.format(year=year)
    base = Path(exporter_folder)
    return base / subpath if subpath else base


def file_code(code: str, settings=None) -> str:
    configured = (settings.get("file_codes") or {}) if settings else {}
    return configured.get(code, DEFAULT_FILE_CODES.get(code, code))


# -- shipment folder scanning -------------------------------------------

def _sequence_from_documents(folder: Path) -> int | None:
    """Read the shipment sequence out of the folder's own document names.

    More reliable than the folder's numeric prefix, which is occasionally wrong
    (see the module docstring). Returns None when no document identifies one.
    """
    try:
        entries = list(folder.iterdir())
    except OSError:
        return None

    for entry in entries:
        if entry.is_file() and is_main_workbook(entry.name):
            sequence = main_workbook_sequence(entry.name)
            if sequence is not None:
                return sequence

    # INDO has no main workbook; its billing file (Inv-IBR01.xlsx) carries the
    # sequence instead.
    for entry in entries:
        if entry.is_file() and is_invoice(entry.name):
            match = INVOICE_RE.match(entry.name)
            if match:
                return int(match.group("seq"))
    return None


def scan_shipment_folders(exporter_folder, code: str, year: int,
                          settings=None) -> list[tuple[int, Path]]:
    """Return [(sequence, path)] sorted ascending for one exporter and year."""
    directory = shipment_dir(exporter_folder, code, year, settings)
    if not directory.is_dir():
        return []

    results: list[tuple[int, Path]] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_dir():
            continue
        prefix = SEQ_PREFIX_RE.match(entry.name)
        if not prefix:
            continue  # e.g. the "1-30" archive folder in AMJ
        sequence = _sequence_from_documents(entry)
        if sequence is None:
            sequence = int(prefix.group(1))
        results.append((sequence, entry))
    return sorted(results, key=lambda pair: pair[0])


def latest_shipment_folder(exporter_folder, code: str, year: int,
                           settings=None) -> tuple[int, Path] | None:
    folders = scan_shipment_folders(exporter_folder, code, year, settings)
    return folders[-1] if folders else None


def template_source_folder(exporter_folder, code: str, year: int,
                           destination: str | None,
                           settings=None) -> Path | None:
    """The folder to copy as a template for a new shipment.

    Prefer the most recent this-year folder whose name mentions the destination
    port; otherwise fall back to the last folder overall (the prior behaviour).
    The match is a case-insensitive substring, mirroring how the app writes
    folder names ("...-karachi-...").
    """
    folders = scan_shipment_folders(exporter_folder, code, year, settings)
    if not folders:
        return None
    dest = (destination or "").strip().lower()
    if dest:
        matches = [path for _, path in folders if dest in path.name.lower()]
        if matches:
            return matches[-1]
    return folders[-1][1]


def sequence_prefix_width(folder_name: str) -> int:
    """How many digits the folder's numeric prefix uses, e.g. "04." -> 2.

    Used to keep a new folder's padding consistent with its neighbours.
    """
    match = SEQ_PREFIX_RE.match(str(folder_name))
    return len(match.group(1)) if match else 1


def next_sequence_number(exporter_folder, code: str, year: int,
                         settings=None) -> int:
    latest = latest_shipment_folder(exporter_folder, code, year, settings)
    return latest[0] + 1 if latest else 1


# -- filename derivation -------------------------------------------------

def _renumber(old_sequence_text: str, new_sequence: int) -> str:
    """Reuse the source's zero-padding: 04 -> 05, 23 -> 24, 9 -> 10."""
    return str(new_sequence).zfill(len(old_sequence_text))


def derive_main_workbook_name(source_folder, new_sequence: int,
                              destination_port: str = "") -> str | None:
    """Canonical name for the copied main workbook:

        {code}{seq} - {destination} - VGM,SI,Inv,PL{ext}

    The exporter code, zero-padding and extension come from the source workbook
    (so an AMJ `.xls` stays `.xls`); only the sequence and destination are
    written fresh. Returns None when the folder has no main workbook (INDO).
    """
    for entry in sorted(Path(source_folder).iterdir()):
        if not entry.is_file() or not is_main_workbook(entry.name):
            continue
        prefix = _CODE_SEQ_RE.match(entry.name)
        code = prefix.group("code") if prefix else ""
        seq = (_renumber(prefix.group("seq"), new_sequence)
               if prefix else str(new_sequence))
        dest = (destination_port or "").strip()
        middle = f" - {dest}" if dest else ""
        return f"{code}{seq}{middle} - VGM,SI,Inv,PL{entry.suffix}"
    return None


def derive_invoice_name(source_folder, new_sequence: int) -> str | None:
    """New name for the billing workbook, shaped like the source's.

    Handles both `Invoice tagihan AMJ23.xlsx` and `Inv-TTJ04.xlsx`.
    """
    for entry in sorted(Path(source_folder).iterdir()):
        if not entry.is_file():
            continue
        if is_main_workbook(entry.name):
            continue  # the main workbook also ends in a code+digits pattern
        if not re.search(r"inv", entry.name, re.IGNORECASE):
            continue
        match = INVOICE_RE.match(entry.name)
        if match:
            start, end = match.span("seq")
            seq = _renumber(match.group("seq"), new_sequence)
            return entry.name[:start] + seq + entry.name[end:]
    return None


def source_subfolders(source_folder) -> list[str]:
    """The subfolder names actually present, since the set varies per exporter.

    PRD 9.2 hardcodes six names; TTJ adds "Ke Mandiri", NIT has no "Fumi",
    THREESTAR has neither "Fumi" nor "PEB & NPE", and spelling differs
    ("Draf" vs "Draft"). Clearing whatever is really there is correct and
    satisfies PRD Section 14's "subfolder missing — log and continue".
    """
    return sorted(e.name for e in Path(source_folder).iterdir() if e.is_dir())
