"""Discover active shipments by scanning the Drive (folder-scan tracker).

Replaces the old "construct a shipment folder" flow: instead of building a
shipment, the app finds the ones already on disk. Each scan produces a *plan* —
which `{code}{seq}` folders to import as new active shipments and which are
already complete — and leaves the database writes to the caller (see
ui/scan_controller.py). Keeping the plan pure makes the discovery, done-
detection and contiguous-run rules testable without a database or Excel.

Rules (confirmed with the user, 2026-08):
* Done — a shipment whose "send" subfolder ("Dok kirim" / "Doc Kirim") holds an
  export document (invoice, packing list, COO, or BL/waybill) is complete and is
  never imported. See is_done for why the BL itself is not detected directly.
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

# A completed shipment's export documents land in its "send" subfolder — the
# spelling and case both vary ("Dok kirim", "Doc Kirim").
_SEND_FOLDERS = ("dok kirim", "doc kirim")
# The Bill of Lading itself is named too many ways to detect — OBL/OHBL/SWB,
# named by the carrier's BL number (OOLU…, BLW…, MEDU…), and usually an image
# scan with no text layer. So completion is read from the export documents that
# are always co-filed with it. Any one of these marks the shipment done; a Fumi
# or Phyto certificate alone is an early step and does not (confirmed with the
# user against NIT, 2026-08).
_EXPORT_DOC = re.compile(
    r"\binv(oice)?\b"                                   # commercial invoice
    r"|\bpl\b|\bp\.?\s*list\b|packing"                  # packing list
    r"|\bcoo\b|certificate of origin|\bform\s*[dea]\b"  # certificate of origin
    r"|\b[omh]*bl\b|\bswb\b|waybill|telex",             # bill of lading / waybill
    re.IGNORECASE)


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

def _find_send_folder(folder):
    for entry in drive._safe_iterdir(folder):     # folder: local path or Drive node
        if entry.is_dir() and entry.name.strip().lower() in _SEND_FOLDERS:
            return entry
    return None


def is_done(folder) -> bool:
    """True when the shipment's send folder holds an export document.

    The BL is filed under a dozen different names and is often an image scan with
    no text, so rather than identify it we take any of the export documents that
    are co-filed with it at completion — the commercial invoice, packing list,
    COO, or a recognisable BL/waybill. A Fumi or Phyto certificate alone is an
    early step and does not count; an empty send folder is an active shipment.
    Purely filename-based, so a scan opens no PDFs.
    """
    send = _find_send_folder(folder)
    if send is None:
        return False
    return any(entry.is_file() and _EXPORT_DOC.search(entry.name)
               for entry in drive._safe_iterdir(send))


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
         settings=None) -> ScanResult:
    """Plan a scan of the Drive: which folders to import, which are done.

    `registered` is the set of `(code, seq)` the tracker already knows; those are
    skipped so a scan only adds newly-eligible folders.
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
            if is_done(candidate.folder):
                result.done.append(candidate)
            else:
                result.to_import.append(candidate)
    return result
