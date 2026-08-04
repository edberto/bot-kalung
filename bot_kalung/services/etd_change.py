"""ETD migration (2026-07-22).

The ETD is set once from the DO and was never editable, yet a lot derives from
it:

* the folder name ends with the ETD's day and month (`…-03 aug`),
* `document_number(seq, etd)` is `{seq}{MM}{YYYY}`, so the **VGM number** and
  **SI title** change whenever the ETD moves to another month or year,
* the VGM **DATE** cell (`vgm_date_month`, which rolls to the next month in the
  last three days, so it can change even within one month),
* the SI **ETD** cell (`etd_long`).

Simpler than `resequence`: the workbook, invoice and PDF filenames embed the
*sequence*, not the ETD, so nothing inside the folder is renamed — only the
folder itself and four cells.

Same check-then-run shape as `resequence`, and it shares that module's
`is_locked` so both pre-flights behave identically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import drive, naming
from .resequence import Blocker, Result, is_locked

# The trailing "-03 aug" that folder_name() appends from the ETD.
_DATE_SUFFIX_RE = re.compile(r"-\d{1,2}\s+[A-Za-z]{3}$")


@dataclass
class EtdPlan:
    shipment_id: str
    label: str
    sequence: int
    old_etd: date | None
    new_etd: date
    folder: Path | None = None
    new_folder: Path | None = None
    old_document_number: str = ""
    new_document_number: str = ""
    new_vgm_date: str = ""
    new_si_etd: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def document_number_changes(self) -> bool:
        return bool(self.new_document_number) and \
            self.old_document_number != self.new_document_number


def _renamed_suffix(folder_name: str, new_etd: date) -> str:
    """Swap only the trailing date segment, keeping the rest of the name.

    Mirrors `resequence._renamed_prefix`, which swaps the leading number: a
    folder that was renamed by hand keeps whatever else it says.
    """
    replacement = f"-{new_etd.day:02d} {naming.MONTHS_ABBR[new_etd.month - 1]}"
    if _DATE_SUFFIX_RE.search(folder_name):
        return _DATE_SUFFIX_RE.sub(replacement, folder_name)
    return folder_name          # no recognisable date segment — leave it alone


def build_plan(row, new_etd: date) -> EtdPlan:
    """Work out exactly what a new ETD changes for this shipment."""
    plan = EtdPlan(
        shipment_id=row["id"],
        label=f"{row['exporter_code']}{row['sequence_number']}",
        sequence=row["sequence_number"],
        old_etd=naming.parse_iso_date(row["etd_belawan"]),
        new_etd=new_etd)

    plan.new_document_number = naming.document_number(plan.sequence, new_etd)
    plan.new_vgm_date = naming.vgm_date_month(new_etd)
    plan.new_si_etd = naming.etd_long(new_etd)
    if plan.old_etd is not None:
        plan.old_document_number = naming.document_number(
            plan.sequence, plan.old_etd)

    folder = Path(row["folder_path"]) if row["folder_path"] else None
    if folder is None or not folder.is_dir():
        plan.notes.append("Folder tidak ditemukan — hanya data yang diubah.")
        return plan

    plan.folder = folder
    new_name = _renamed_suffix(folder.name, new_etd)
    if new_name != folder.name:
        plan.new_folder = folder.parent / new_name
    else:
        plan.notes.append(
            "Nama folder tidak memuat tanggal — nama folder tidak diubah.")
    return plan


def preflight(plan: EtdPlan) -> list[Blocker]:
    """Open workbooks and destination collisions, so nothing is half-applied."""
    blockers: list[Blocker] = []
    if plan.folder is None:
        return blockers

    for lock in plan.folder.glob("~$*"):
        blockers.append(Blocker(plan.label, lock.name,
                                "File Excel sedang dibuka. Tutup dulu."))
    workbook = _workbook(plan.folder)
    if workbook is not None and is_locked(workbook):
        blockers.append(Blocker(plan.label, workbook.name,
                                "File sedang dipakai program lain. Tutup dulu."))
    if plan.new_folder is not None and plan.new_folder.exists():
        blockers.append(Blocker(plan.label, plan.new_folder.name,
                                "Nama folder tujuan sudah dipakai."))
    return blockers


def _workbook(folder: Path) -> Path | None:
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and drive.is_main_workbook(entry.name):
            return entry
    return None


def execute(db, plan: EtdPlan) -> Result:
    """Rewrite Excel, rename the folder, then update the row last. Never raises."""
    result = Result()
    for note in plan.notes:
        result.warnings.append(f"{plan.label}: {note}")

    if plan.folder is not None:
        _rewrite_cells(plan, result)

    new_folder_path = None
    if plan.new_folder is not None:
        try:
            plan.folder.rename(plan.new_folder)
            new_folder_path = plan.new_folder
            result.changed.append(
                f"{plan.label}: folder → {plan.new_folder.name}")
        except OSError as exc:
            result.failed.append(
                f"{plan.label}: gagal mengganti nama folder ({exc})")

    try:
        if new_folder_path is not None:
            db.execute(
                "UPDATE shipments SET etd_belawan=?, folder_path=? WHERE id=?",
                (plan.new_etd.isoformat(), str(new_folder_path),
                 plan.shipment_id))
        else:
            db.execute("UPDATE shipments SET etd_belawan=? WHERE id=?",
                       (plan.new_etd.isoformat(), plan.shipment_id))
        result.changed.append(
            f"{plan.label}: ETD → {plan.new_etd.isoformat()}")
    except Exception as exc:      # noqa: BLE001 - reported, never raised
        result.failed.append(f"{plan.label}: gagal memperbarui data ({exc})")

    result.warnings.append(
        f"{plan.label}: PDF yang sudah diekspor masih memuat ETD lama — "
        "ekspor ulang bila perlu.")
    return result


def _rewrite_cells(plan: EtdPlan, result: Result) -> None:
    """VGM number, VGM DATE, SI title and SI ETD."""
    from . import excel

    workbook = _workbook(plan.folder)
    if workbook is None:
        result.warnings.append(
            f"{plan.label}: workbook tidak ditemukan — sel Excel tidak diubah.")
        return

    try:
        with excel.open_book(workbook) as book:
            report = excel.PrefillReport()
            vgm = excel.find_sheet(book, "VGM")
            if vgm is not None:
                cell = (excel.find_label(vgm, "NO :", column=2)
                        or excel.find_label(vgm, "VGM-", column=2))
                if cell:
                    excel._set(vgm, cell[0], cell[1],
                               f"NO : {naming.vgm_number(plan.sequence, plan.new_etd)}",
                               as_text=True, report=report)
                date_cell = excel.find_label(vgm, "DATE", column=2, exact=True)
                if date_cell:
                    excel._set(vgm, date_cell[0], 5, plan.new_vgm_date,
                               red=True, as_text=True, report=report)

            si = excel.find_sheet(book, "SI")
            if si is not None:
                title = excel.find_label(si, "SHIPPING INSTRUCTION")
                if title:
                    # A formula-linked title updates itself; _set skips it.
                    excel._set(si, title[0], title[1],
                               naming.si_title(plan.sequence, plan.new_etd),
                               as_text=True, report=report)
                etd_cell = excel.find_label(si, "ETD", exact=True)
                if etd_cell:
                    column = excel.value_column_after(si, etd_cell[0], etd_cell[1])
                    excel._set(si, etd_cell[0], column, plan.new_si_etd,
                               as_text=True, report=report)
            book.save()
        result.changed.append(
            f"{plan.label}: sel Excel diperbarui (VGM DATE {plan.new_vgm_date}, "
            f"ETD SI {plan.new_si_etd})")
        if plan.document_number_changes:
            result.changed.append(
                f"{plan.label}: nomor dokumen {plan.old_document_number} → "
                f"{plan.new_document_number}")
    except Exception as exc:      # noqa: BLE001 - reported, never raised
        result.failed.append(
            f"{plan.label}: gagal memperbarui sel Excel ({exc})")
