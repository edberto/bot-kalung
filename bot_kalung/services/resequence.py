"""Sequence-number migration (2026-07-21).

Correcting a shipment's sequence number touches four places, because nothing
derived from it is stored in the database:

* the shipment folder's numeric prefix on Drive,
* the main workbook and invoice filenames (the app reads the sequence from the
  *workbook filename* first, so renaming the folder alone would leave the old
  number authoritative and corrupt "next sequence"),
* the document numbers inside the workbook (VGM number and SI title),
* already-exported PDFs, whose names embed the number.

Email subjects and folder-name variables render live from the database row, so
they correct themselves once the row is updated.

Clashes are resolved interactively: the caller asks the user where each
displaced shipment should go, until `find_clash` returns None. Only then is a
plan built, pre-flighted for locked files, and executed.

The folder is renamed by swapping its numeric prefix rather than rebuilding the
name from scratch, so any hand-made part of the folder name survives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import drive, naming

# "23." at the start of a shipment folder name.
_PREFIX_RE = re.compile(r"^(\d+)\.")


@dataclass
class Clash:
    """A number two shipments would both hold."""
    sequence: int
    shipment_id: str
    label: str
    exporter_code: str


@dataclass
class Rename:
    source: Path
    target: Path


@dataclass
class Move:
    shipment_id: str
    label: str
    exporter_code: str
    old_sequence: int
    new_sequence: int
    folder: Path | None = None
    new_folder: Path | None = None
    file_renames: list[Rename] = field(default_factory=list)
    pdf_renames: list[Rename] = field(default_factory=list)
    old_document_number: str = ""
    new_document_number: str = ""
    etd = None
    notes: list[str] = field(default_factory=list)

    @property
    def new_label(self) -> str:
        return f"{self.exporter_code}{self.new_sequence}"


@dataclass
class Blocker:
    label: str
    path: str
    reason: str


@dataclass
class Result:
    changed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def label_of(row) -> str:
    return f"{row['exporter_code']}{row['sequence_number']}"


# -- clash detection ---------------------------------------------------------

def find_clash(rows, moves: dict[str, int]) -> Clash | None:
    """The first unresolved number collision, or None when the assignment is
    conflict-free.

    Sequences are per-exporter, so AMJ24 and TTJ24 never clash. A shipment that
    is already being moved is only reported once every other holder of that
    number has been dealt with, so the caller keeps asking about *new*
    shipments rather than looping on one it already answered for.
    """
    by_id = {r["id"]: r for r in rows}
    final: dict[str, int] = {
        r["id"]: moves.get(r["id"], r["sequence_number"]) for r in rows}

    grouped: dict[tuple[str, int], list[str]] = {}
    for shipment_id, sequence in final.items():
        key = (by_id[shipment_id]["exporter_code"], sequence)
        grouped.setdefault(key, []).append(shipment_id)

    for (code, sequence), ids in sorted(grouped.items(), key=lambda kv: kv[0][1]):
        if len(ids) < 2:
            continue
        unmoved = [i for i in ids if i not in moves]
        target = unmoved[0] if unmoved else ids[-1]
        row = by_id[target]
        return Clash(sequence=sequence, shipment_id=target,
                     label=label_of(row), exporter_code=code)
    return None


# -- planning ----------------------------------------------------------------

def _renamed_prefix(folder_name: str, new_sequence: int) -> str:
    """Swap only the numeric prefix, keeping the exporter's own padding."""
    match = _PREFIX_RE.match(folder_name)
    if not match:
        return folder_name
    width = len(match.group(1))
    return _PREFIX_RE.sub(f"{new_sequence:0{width}d}.", folder_name, count=1)


def build_plan(rows, moves: dict[str, int]) -> list[Move]:
    """Work out exactly what changes for each shipment being renumbered."""
    by_id = {r["id"]: r for r in rows}
    plan: list[Move] = []

    for shipment_id, new_sequence in moves.items():
        row = by_id.get(shipment_id)
        if row is None:
            continue
        move = Move(
            shipment_id=shipment_id, label=label_of(row),
            exporter_code=row["exporter_code"],
            old_sequence=row["sequence_number"], new_sequence=new_sequence)
        move.etd = naming.parse_iso_date(row["etd_belawan"])

        if move.etd is not None:
            move.old_document_number = naming.document_number(
                move.old_sequence, move.etd)
            move.new_document_number = naming.document_number(
                new_sequence, move.etd)
        else:
            move.notes.append(
                "ETD tidak diketahui — nomor dokumen di Excel tidak diubah.")

        folder = Path(row["folder_path"]) if row["folder_path"] else None
        if folder is None or not folder.is_dir():
            move.notes.append("Folder tidak ditemukan — hanya data yang diubah.")
            plan.append(move)
            continue

        move.folder = folder
        new_name = _renamed_prefix(folder.name, new_sequence)
        if new_name != folder.name:
            move.new_folder = folder.parent / new_name

        # Workbook + invoice: derive the new names from what is actually there.
        for derive in (drive.derive_main_workbook_name, drive.derive_invoice_name):
            new_file = derive(folder, new_sequence)
            if not new_file:
                continue
            old_file = _matching_source(folder, derive, new_sequence)
            if old_file is not None and old_file.name != new_file:
                move.file_renames.append(
                    Rename(old_file, old_file.parent / new_file))

        # Exported PDFs carry the label, e.g. "SI - AMJ23.pdf".
        pdf_dir = folder / "PDF"
        if pdf_dir.is_dir():
            suffix = f" - {move.exporter_code}{move.old_sequence}.pdf"
            replacement = f" - {move.exporter_code}{new_sequence}.pdf"
            for pdf in sorted(pdf_dir.glob("*.pdf")):
                if pdf.name.endswith(suffix):
                    move.pdf_renames.append(Rename(
                        pdf, pdf.parent / (pdf.name[:-len(suffix)] + replacement)))
        plan.append(move)
    return plan


def _matching_source(folder: Path, derive, new_sequence: int) -> Path | None:
    """The file `derive` was reading — the one whose name it renumbered."""
    is_main = derive is drive.derive_main_workbook_name
    for entry in sorted(folder.iterdir()):
        if not entry.is_file():
            continue
        if is_main:
            if drive.MAIN_WORKBOOK_RE.match(entry.name):
                return entry
        else:
            if drive.MAIN_WORKBOOK_RE.match(entry.name):
                continue
            if re.search(r"inv", entry.name, re.IGNORECASE) and \
                    drive.INVOICE_RE.match(entry.name):
                return entry
    return None


# -- pre-flight --------------------------------------------------------------

def _is_locked(path: Path) -> bool:
    """True when another process holds the file open (Excel, a viewer…)."""
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def preflight(plan: list[Move]) -> list[Blocker]:
    """Everything that would stop the migration, so the worker can fix it and
    retry rather than discovering a half-done rename.
    """
    blockers: list[Blocker] = []
    for move in plan:
        if move.folder is None:
            continue
        # An open workbook leaves a "~$name.xlsx" lock file beside it.
        for lock in move.folder.glob("~$*"):
            blockers.append(Blocker(
                move.label, str(lock.name),
                "File Excel sedang dibuka. Tutup dulu."))
        for rename in move.file_renames + move.pdf_renames:
            if _is_locked(rename.source):
                blockers.append(Blocker(
                    move.label, rename.source.name,
                    "File sedang dipakai program lain. Tutup dulu."))
            if rename.target.exists():
                blockers.append(Blocker(
                    move.label, rename.target.name,
                    "Nama file tujuan sudah dipakai."))
        if move.new_folder is not None and move.new_folder.exists():
            blockers.append(Blocker(
                move.label, move.new_folder.name,
                "Nama folder tujuan sudah dipakai."))
    return blockers


# -- execution ---------------------------------------------------------------

def execute(db, plan: list[Move]) -> Result:
    """Apply the plan. Files first, then the database, so the row is only
    updated once its folder actually moved. Never raises.
    """
    result = Result()
    for move in plan:
        for note in move.notes:
            result.warnings.append(f"{move.label}: {note}")

        folder = move.folder
        renamed_files = 0
        # 1. Files inside the folder (before the folder itself moves).
        for rename in move.file_renames + move.pdf_renames:
            try:
                rename.source.rename(rename.target)
                renamed_files += 1
            except OSError as exc:
                result.failed.append(
                    f"{move.label}: gagal mengganti nama {rename.source.name} ({exc})")

        # 2. Document numbers inside the workbook.
        if folder is not None and move.new_document_number:
            _rewrite_document_number(folder, move, result)

        # 3. The folder itself.
        new_folder_path = None
        if move.new_folder is not None:
            try:
                move.folder.rename(move.new_folder)
                new_folder_path = move.new_folder
            except OSError as exc:
                result.failed.append(
                    f"{move.label}: gagal mengganti nama folder ({exc})")

        # 4. The database row last.
        try:
            if new_folder_path is not None:
                db.execute(
                    "UPDATE shipments SET sequence_number=?, folder_path=? "
                    "WHERE id=?",
                    (move.new_sequence, str(new_folder_path), move.shipment_id))
            else:
                db.execute(
                    "UPDATE shipments SET sequence_number=? WHERE id=?",
                    (move.new_sequence, move.shipment_id))
            result.changed.append(
                f"{move.label} → {move.new_label}"
                + (f" ({renamed_files} berkas)" if renamed_files else ""))
        except Exception as exc:      # noqa: BLE001 - reported, never raised
            result.failed.append(f"{move.label}: gagal memperbarui data ({exc})")

        if move.pdf_renames:
            result.warnings.append(
                f"{move.label}: {len(move.pdf_renames)} PDF diganti namanya, "
                "tetapi isinya masih memuat nomor lama — ekspor ulang bila perlu.")
    return result


def _rewrite_document_number(folder: Path, move: Move, result: Result) -> None:
    """Update the VGM number and SI title inside the workbook."""
    from . import excel

    workbook = None
    for entry in sorted(folder.iterdir()):
        # After the rename above the file already carries the new sequence.
        if entry.is_file() and drive.MAIN_WORKBOOK_RE.match(entry.name):
            workbook = entry
            break
    if workbook is None:
        result.warnings.append(
            f"{move.label}: workbook tidak ditemukan — nomor dokumen tidak diubah.")
        return

    try:
        with excel.open_book(workbook) as book:
            report = excel.PrefillReport()
            vgm = excel.find_sheet(book, "VGM")
            if vgm is not None:
                cell = (excel.find_label(vgm, "NO :", column=2)
                        or excel.find_label(vgm, "VGM-", column=2))
                if cell:
                    value = f"NO : {naming.vgm_number(move.new_sequence, move.etd)}"
                    excel._set(vgm, cell[0], cell[1], value, as_text=True,
                               report=report)
            si = excel.find_sheet(book, "SI")
            if si is not None:
                title = excel.find_label(si, "SHIPPING INSTRUCTION")
                if title:
                    # A formula-linked title updates itself; _set skips it.
                    excel._set(si, title[0], title[1],
                               naming.si_title(move.new_sequence, move.etd),
                               as_text=True, report=report)
            book.save()
        result.changed.append(
            f"{move.label}: nomor dokumen {move.old_document_number} → "
            f"{move.new_document_number}")
    except Exception as exc:      # noqa: BLE001 - reported, never raised
        result.failed.append(
            f"{move.label}: gagal memperbarui nomor dokumen di Excel ({exc})")
