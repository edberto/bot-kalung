"""Export the document sheets to PDF (PRD Section 6.3, step E4).

Pulled forward from Phase 2 at the user's request (2026-07-20). Read-only as
far as the workbook is concerned: `ExportAsFixedFormat` honours whatever page
setup and print area the sheet already has, and the app never touches either.

The PRD lists four documents ("SI, VGM, Invoice, PL") but every live workbook
carries *two* invoice sheets — `Inv Buyer` (the buyer's copy) and `Inv BC`
(Bea Cukai / customs). Both are exported, since leaving one out would silently
drop a document the worker needs; see DECISIONS.md section 17.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import excel

# (document name used in the filename, sheet-name prefix to look for).
# Order is deliberate: "Inv BC" must be tried before the broader "Inv", and
# these prefixes are matched after whitespace is collapsed, so they cover
# 'Inv Buyer ', 'Inv  BC', 'P.List Buyer' and 'P.List buyer' alike.
DOCUMENTS = [
    ("SI", "SI"),
    ("VGM", "VGM"),
    ("Inv Buyer", "Inv Buyer"),
    ("Inv BC", "Inv BC"),
    ("PL", "P.List"),
]

PDF_SUBFOLDER = "PDF"


@dataclass
class ExportResult:
    written: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.written) and not self.failed

    def message(self) -> str:
        """One Indonesian line for the toast."""
        if not self.written and not self.failed:
            return "Tidak ada sheet dokumen yang bisa diekspor."
        parts = [f"{len(self.written)} PDF dibuat"]
        if self.missing:
            parts.append(f"sheet tidak ditemukan: {', '.join(self.missing)}")
        if self.failed:
            parts.append(f"gagal: {', '.join(self.failed)}")
        return ". ".join(parts) + "."


def pdf_filename(document: str, exporter_code: str, sequence: int) -> str:
    """`SI - AMJ24.pdf` (user-specified format, 2026-07-20).

    The sequence is NOT zero-padded here — this is the shipment label the team
    already uses in folder names and email subjects, not the document number
    from `naming.document_number`, which is padded.
    """
    return f"{document} - {exporter_code}{sequence}.pdf"


def target_dir(folder) -> Path:
    return Path(folder) / PDF_SUBFOLDER


def export_workbook(book, *, exporter_code: str, sequence: int,
                    out_dir) -> ExportResult:
    """Export each document sheet of an already-open workbook."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = ExportResult()

    for document, prefix in DOCUMENTS:
        sheet = excel.find_sheet(book, prefix)
        if sheet is None:
            result.missing.append(document)
            continue
        path = out / pdf_filename(document, exporter_code, sequence)
        try:
            # Type=0 is xlTypePDF. Overwrites silently, which is what a
            # re-export after a correction should do.
            sheet.api.ExportAsFixedFormat(0, str(path))
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            result.failed.append(f"{document} ({exc})")
            continue
        if path.is_file():
            result.written.append(path)
        else:
            result.failed.append(document)
    return result


def export_shipment(workbook_path, *, exporter_code: str, sequence: int,
                    folder=None) -> ExportResult:
    """Open the workbook if needed and export. Never raises."""
    path = Path(workbook_path)
    result = ExportResult()
    if not path.is_file():
        result.failed.append(f"file Excel tidak ditemukan: {path.name}")
        return result

    out = target_dir(folder if folder is not None else path.parent)
    try:
        with excel.open_book(path) as book:
            return export_workbook(book, exporter_code=exporter_code,
                                   sequence=sequence, out_dir=out)
    except Exception as exc:  # noqa: BLE001 - E4 must never kill the app
        result.failed.append(str(exc))
        return result
