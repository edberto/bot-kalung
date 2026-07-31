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

# Matches the main workbook across every exporter's convention seen on Drive:
#   AMJ23-VGM,SI,Inv,PL.xls        TTJ04-Karachi-VGM,SI,INV,PL.xlsx
#   NIT15-CHENNAI-VGM,SI,INV,PL.xlsx   AMJ09-VGM,SI,Inv,P.List.xls
#   LSM01-1x20-VGM,SI,Inv,P.List.xlsx  AMJ18VGM,SI,Inv,PL.xls
#   AMJ29-Vgm,SI,Inv,PL......xls        GGN-00004-VGM,SI,Inv,PL.xls
# Tolerates `P.List`/`PList` for `PL`, a missing hyphen before VGM, a hyphen
# between code and sequence, trailing dots/spaces, and stray spaces around the
# commas. Deliberately does NOT match copy suffixes like " (1).xlsx", so the
# real workbook wins over a duplicate.
MAIN_WORKBOOK_RE = re.compile(
    r"^(?P<code>[A-Za-z]+)-?(?P<seq>\d+)(?P<middle>.*?)-?\s*"
    r"VGM\s*,\s*SI\s*,\s*INV\s*,\s*(?:P\.?\s*LIST|PL)[.\s]*(?P<ext>\.xlsx?)$",
    re.IGNORECASE,
)

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
        if not entry.is_file():
            continue
        match = MAIN_WORKBOOK_RE.match(entry.name)
        if match:
            return int(match.group("seq"))

    # INDO has no main workbook; its billing file (Inv-IBR01.xlsx) carries the
    # sequence instead.
    for entry in entries:
        if not entry.is_file():
            continue
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


def derive_main_workbook_name(source_folder, new_sequence: int) -> str | None:
    """New name for the VGM/SI/Inv/PL workbook, shaped like the source's.

    Preserves extension, embedded destination, letter case, and zero-padding —
    so `TTJ04-Karachi-VGM,SI,INV,PL.xlsx` becomes `TTJ05-Karachi-...`.
    Returns None when the exporter has no such workbook (INDO).
    """
    for entry in sorted(Path(source_folder).iterdir()):
        if not entry.is_file():
            continue
        match = MAIN_WORKBOOK_RE.match(entry.name)
        if not match:
            continue
        # Replace only the sequence characters, so everything else survives
        # untouched: the code, any hyphen between code and seq (GGN-00004), the
        # embedded destination, the "P.List" spelling, trailing dots, extension,
        # and the source's own casing ("Inv,PL" vs "INV,PL").
        start, end = match.span("seq")
        seq = _renumber(match.group("seq"), new_sequence)
        return entry.name[:start] + seq + entry.name[end:]
    return None


def derive_invoice_name(source_folder, new_sequence: int) -> str | None:
    """New name for the billing workbook, shaped like the source's.

    Handles both `Invoice tagihan AMJ23.xlsx` and `Inv-TTJ04.xlsx`.
    """
    for entry in sorted(Path(source_folder).iterdir()):
        if not entry.is_file():
            continue
        if MAIN_WORKBOOK_RE.match(entry.name):
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
