"""Discover active shipments by scanning the Drive (folder-scan tracker).

Replaces the old "construct a shipment folder" flow: instead of building a
shipment, the app finds the ones already on disk. Each scan produces a *plan* —
which `{code}{seq}` folders to import as new active shipments and which are
already complete — and leaves the database writes to the caller (see
ui/scan_controller.py). Keeping the plan pure makes the discovery, done-
detection and contiguous-run rules testable without a database or Excel.

Rules (confirmed with the user, 2026-08):
* Done — a folder whose "Dok kirim" subfolder holds a Bill of Lading scan is
  complete and is never imported.
* In sequence — only the largest unbroken run of sequences is eligible, so a
  stray outlier (an old folder far below, or a folder jumped ahead of the run)
  is skipped. Sequences come from the folder's numeric prefix (the source of
  truth), not the workbook, which is sometimes misnumbered.
* Delta — a `(code, seq)` already in the registry is left alone (updates and
  deletions are manual), so each scan only adds newly-eligible folders.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import drive

# The completed Bill of Lading lands in this subfolder (spelling is stable
# across exporters, but the case is not). The real files are named with a BL/OBL
# token — "NIT01-OBL.pdf", "CKJ15-BL.pdf", "BL ORIGINAL ....pdf" — never the
# "SCAN" the spec first assumed (confirmed against the live Drive, 2026-08).
_DOK_KIRIM = "dok kirim"
# BL, or O/M/H-BL (Original / Master / House), as a standalone token.
_BL_NAME = re.compile(r"\b[OMH]?BL\b", re.IGNORECASE)
_BL_PAGE_MARKER = "BILL OF LADING"


@dataclass
class Candidate:
    """A `{code}{seq}` shipment folder the scan has identified."""
    code: str
    sequence: int
    folder: Path
    series_label: str

    @property
    def label(self) -> str:
        return f"{self.code}{self.sequence}"


@dataclass
class ScanResult:
    to_import: list[Candidate] = field(default_factory=list)   # new, not done
    done: list[Candidate] = field(default_factory=list)        # complete (skip)
    report: list[str] = field(default_factory=list)            # discovery notes


# -- done-detection (Bill of Lading scan) --------------------------------

def _looks_like_bl(name: str) -> bool:
    """A PDF whose name marks it as the Bill of Lading."""
    if not name.lower().endswith(".pdf"):
        return False
    upper = name.upper()
    return bool(_BL_NAME.search(upper)) or _BL_PAGE_MARKER in upper


def _has_bl_marker(text: str) -> bool:
    """Whitespace-tolerant — the marker often wraps as "BILL OF\\nLADING"."""
    return _BL_PAGE_MARKER in re.sub(r"\s+", " ", text).upper()


def _default_page1_text(path: Path) -> str:
    """First-page text of a PDF, or "" on any failure. Never raises."""
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            if not pdf.pages:
                return ""
            return pdf.pages[0].extract_text() or ""
    except Exception:      # noqa: BLE001 - a scan must never crash a poll
        return ""


def _find_subfolder(folder: Path, name_lower: str) -> Path | None:
    for entry in drive._safe_iterdir(folder):
        if entry.is_dir() and entry.name.strip().lower() == name_lower:
            return entry
    return None


def is_done(folder, *, page1_text=_default_page1_text) -> bool:
    """True when the shipment's Bill of Lading has arrived.

    A PDF in the (case-insensitive) "Dok kirim" subfolder whose name carries a
    BL/OBL token. When the PDF has a text layer we confirm the "BILL OF LADING"
    page-1 marker (so a BL-named non-BL PDF does not count); a scanned-image BL
    has no text, so the name in the send folder is taken as enough. `page1_text`
    is injectable so the rule can be tested without real PDFs.
    """
    dok = _find_subfolder(Path(folder), _DOK_KIRIM)
    if dok is None:
        return False
    for entry in drive._safe_iterdir(dok):
        if not (entry.is_file() and _looks_like_bl(entry.name)):
            continue
        text = page1_text(entry)
        if not text.strip() or _has_bl_marker(text):
            return True
    return False


# -- contiguous-run gate -------------------------------------------------

def contiguous_run(present: set[int]) -> set[int]:
    """The largest unbroken run of consecutive sequences.

    The active batch is the biggest contiguous block, so a lone outlier far from
    the pack is skipped either way it sits:
      * a stray old folder below a gap — {4, 14,15,…,22} -> {14,…,22};
      * a folder jumped ahead of the sequence — {20,21,22,25} -> {20,21,22}
        (25 waits until 23 and 24 appear).
    On a tie the more recent (higher) block wins, so {1,2,5,6} -> {5,6}. Empty in,
    empty out.
    """
    if not present:
        return set()
    ordered = sorted(present)
    runs: list[list[int]] = [[ordered[0]]]
    for seq in ordered[1:]:
        if seq == runs[-1][-1] + 1:
            runs[-1].append(seq)
        else:
            runs.append([seq])
    return set(max(runs, key=lambda run: (len(run), run[0])))


# -- the scan ------------------------------------------------------------

def scan(drive_root, year: int, registered: set[tuple[str, int]],
         settings=None, *, page1_text=_default_page1_text) -> ScanResult:
    """Plan a scan of the Drive: which folders to import, which are done.

    `registered` is the set of `(code, seq)` the tracker already knows; those are
    skipped so a scan only adds newly-eligible folders. `page1_text` is threaded
    to `is_done` for testing.
    """
    result = ScanResult()

    # Identify every numbered folder that has a main workbook, grouped by code.
    by_code: dict[str, dict[int, Candidate]] = {}
    for series in drive.discover_series(drive_root, year, settings):
        identified = skipped = 0
        for folder in drive.numbered_folders(series.path):
            identity = drive.shipment_identity(folder)
            if identity is None:      # no VGM/SI/Inv/PL workbook — can't key it
                skipped += 1
                continue
            code, seq = identity
            slot = by_code.setdefault(code, {})
            if seq not in slot:       # first folder wins on a duplicate sequence
                slot[seq] = Candidate(code, seq, folder, series.label)
                identified += 1
        note = f"{series.label}: {identified} teridentifikasi"
        if skipped:
            note += f", {skipped} tanpa workbook dilewati"
        result.report.append(note)

    # Per code, only the largest contiguous run of sequences is eligible;
    # within it, split already-complete folders from ones to import.
    for code, slot in sorted(by_code.items()):
        for seq in sorted(contiguous_run(set(slot))):
            if (code, seq) in registered:
                continue
            candidate = slot[seq]
            if is_done(candidate.folder, page1_text=page1_text):
                result.done.append(candidate)
            else:
                result.to_import.append(candidate)
    return result
