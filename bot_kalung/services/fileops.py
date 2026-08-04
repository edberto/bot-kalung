"""Folder and file operations for a new shipment (PRD Section 9.2).

Operations run in the PRD's exact order. If any step fails after the destination
folder exists, the folder is deleted and the specific error is reported.
"""

from __future__ import annotations

import fnmatch
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..core.constants import KEEP_FILE_PATTERNS
from . import drive


class FileOpError(Exception):
    """Carries an Indonesian, user-presentable message."""


@dataclass
class ShipmentSetupResult:
    destination: Path
    main_workbook: Path | None
    invoice_workbook: Path | None
    steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _keep(filename: str) -> bool:
    """PRD 9.2 step 3 — the main workbook, the invoice and the permits survive.

    The two Excel files are matched by their token set rather than a glob: their
    filenames vary too much between exporters (`.xls`/`.xlsx`, `PL`/`P.List`,
    `Inv-…`/`Invoice tagihan …`) for a fixed pattern to catch every one, and a
    missed file was silently deleted here before the rename step could find it.
    """
    if drive.is_main_workbook(filename) or drive.is_invoice(filename):
        return True
    lowered = filename.lower()
    return any(fnmatch.fnmatch(lowered, pattern.lower())
               for pattern in KEEP_FILE_PATTERNS)


def create_shipment_folder(*, source_folder, target_parent, final_folder_name: str,
                           do_pdf_path, new_sequence: int, destination_port: str = "",
                           progress=None) -> ShipmentSetupResult:
    """Run PRD 9.2 steps 1-7 and return what was produced.

    `progress` is an optional callable taking an Indonesian status string, used
    by the wizard to drive its per-step checkmarks.
    """
    source = Path(source_folder)
    parent = Path(target_parent)
    final = parent / final_folder_name

    if not source.is_dir():
        raise FileOpError(f"Folder sumber tidak ditemukan: {source}")
    if final.exists():
        # PRD Section 14 — name collision must not proceed.
        raise FileOpError(
            f"Folder bernama '{final_folder_name}' sudah ada. "
            "Ubah nomor urut atau periksa folder yang ada.")

    do_pdf = Path(do_pdf_path) if do_pdf_path else None
    if do_pdf is not None and not do_pdf.is_file():
        raise FileOpError(f"File DO tidak ditemukan: {do_pdf}")

    # Derive the new document names before copying anything, so an unsupported
    # exporter layout fails before any filesystem change.
    main_name = drive.derive_main_workbook_name(source, new_sequence, destination_port)
    invoice_name = drive.derive_invoice_name(source, new_sequence)

    result = ShipmentSetupResult(destination=final, main_workbook=None,
                                 invoice_workbook=None)

    def step(message: str):
        result.steps.append(message)
        if progress:
            progress(message)

    # Copy to a temporary name first so a half-copied folder never looks like a
    # finished shipment; renamed to the final name in step 7.
    staging = parent / f".{final_folder_name}.tmp"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    try:
        parent.mkdir(parents=True, exist_ok=True)

        # 1 — copy source folder
        step("Menyalin folder...")
        shutil.copytree(source, staging)

        # 2 — clear the subfolders that came along, keeping the folders
        step("Mengosongkan subfolder...")
        for child in staging.iterdir():
            if not child.is_dir():
                continue
            for item in child.iterdir():
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)

        # 3 — delete loose root files except the keep patterns
        step("Menghapus file lama...")
        for child in staging.iterdir():
            if child.is_file() and not _keep(child.name):
                child.unlink(missing_ok=True)

        # 4 — copy the DO PDF in, preserving its original filename
        if do_pdf is not None:
            step("Menyalin file DO...")
            shutil.copy2(do_pdf, staging / do_pdf.name)

        # 5 — rename the main workbook
        if main_name:
            step("Mengganti nama file Excel...")
            current = _find_one(staging, drive.is_main_workbook)
            if current is None:
                raise FileOpError(
                    "Tidak menemukan file Excel VGM/SI/Inv/PL di folder baru. "
                    "Periksa apakah folder template berisi file tersebut.")
            renamed = staging / main_name
            current.rename(renamed)
            result.main_workbook = renamed
        else:
            result.warnings.append(
                "Folder sumber tidak memiliki file Excel VGM/SI/Inv/PL.")

        # 6 — rename the billing workbook
        if invoice_name:
            step("Mengganti nama file invoice...")
            current = _find_invoice(staging)
            if current is not None:
                renamed = staging / invoice_name
                current.rename(renamed)
                result.invoice_workbook = renamed
        else:
            result.warnings.append("Folder sumber tidak memiliki file invoice.")

        # 7 — rename to the final folder name
        step("Menyelesaikan folder...")
        staging.rename(final)

    except FileOpError:
        _rollback(staging)
        raise
    except Exception as exc:  # noqa: BLE001 - surface the real cause to the user
        _rollback(staging)
        raise FileOpError(f"Gagal menyiapkan folder: {exc}") from exc

    # Re-point the returned paths at the final folder name.
    if result.main_workbook is not None:
        result.main_workbook = final / result.main_workbook.name
    if result.invoice_workbook is not None:
        result.invoice_workbook = final / result.invoice_workbook.name
    return result


def _rollback(staging: Path) -> None:
    """PRD 9.2 — delete the partially-created folder. Never raises."""
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)


def _find_one(folder: Path, predicate) -> Path | None:
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and predicate(entry.name):
            return entry
    return None


def _find_invoice(folder: Path) -> Path | None:
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and drive.is_invoice(entry.name):
            return entry
    return None


def find_permit(folder) -> Path | None:
    """PRD Section 5 — the import permit, which lives in the shipment folder
    rather than the Drive root (confirmed with the user 2026-07-20).
    """
    for entry in sorted(Path(folder).iterdir()):
        if not entry.is_file():
            continue
        name = entry.name.lower()
        if name.endswith(".pdf") and (name.startswith("permit-") or name.startswith("ip-")):
            return entry
    return None


@dataclass
class FolderDeleteResult:
    """Outcome of deleting a shipment's folder."""
    removed: bool                       # the folder is gone (or was never there)
    recycled: bool                      # went to the Recycle Bin, so recoverable
    existed: bool                       # there was a folder to remove
    message: str = ""


def delete_shipment_folder(folder) -> FolderDeleteResult:
    """Send a shipment folder to the Windows Recycle Bin. Never raises.

    Uses the shell SHFileOperation with FOF_ALLOWUNDO so the delete is
    recoverable. Google Drive's virtual G: drive does not always honour the
    Recycle Bin and may delete permanently regardless; there is no reliable way
    to know up front, so the caller warns that recovery is not guaranteed.
    """
    if not folder:
        return FolderDeleteResult(True, False, False,
                                  "Pengiriman ini tidak punya folder.")
    path = Path(folder)
    if not path.exists():
        return FolderDeleteResult(True, False, False,
                                  "Folder sudah tidak ada.")
    if not path.is_dir():
        return FolderDeleteResult(False, False, True,
                                  f"Bukan folder: {path}")

    try:
        from win32com.shell import shell, shellcon
    except ImportError:      # non-Windows / missing pywin32 — never in prod
        return FolderDeleteResult(
            False, False, True,
            "Penghapusan ke Recycle Bin tidak tersedia di sistem ini.")

    flags = (shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION
             | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI)
    try:
        result, aborted = shell.SHFileOperation(
            (0, shellcon.FO_DELETE, str(path.resolve()), None, flags, None, None))
    except Exception as exc:  # noqa: BLE001 - reported, delete must not crash the app
        return FolderDeleteResult(False, False, True,
                                  f"Gagal menghapus folder: {exc}")

    if aborted or result != 0 or path.exists():
        return FolderDeleteResult(
            False, False, True,
            "Folder tidak dapat dihapus. Mungkin sedang dibuka di Excel atau "
            "Explorer. Tutup lalu coba lagi.")
    return FolderDeleteResult(True, True, True, "Folder dipindahkan ke Recycle Bin.")
