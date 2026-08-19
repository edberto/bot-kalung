"""PDF export, step E4 (PRD 6.3, pulled forward from Phase 2 on 2026-07-20).

Runs against COPIES of the live TTJ and AMJ workbooks, so it covers both the
.xlsx and .xls layouts and both sheet-naming styles ('Inv  BC' with two spaces
on AMJ, 'Inv BC' on TTJ). Requires Microsoft Excel. Never touches G:\\.
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.constants import (
    NON_COMPLETING_ACTION_KINDS, STEP_ACTIONS, WORKFLOW_STEPS,
)
from bot_kalung.services import pdf_export

SOURCES = {
    "TTJ": Path(r"G:\My Drive\TASHA-HUSSAIN-MAJEED\2026 Tasha"
                r"\04.3x40-Karachi-Sunli-OOCL2331585571-Integra179E"
                r"\TTJ04-Karachi-VGM,SI,INV,PL.xlsx"),
    "AMJ": Path(r"G:\My Drive\AMJ\2025\01-40"
                r"\31.stuff depo mjb-AMJ31PI-31-1x40-Tuticorin-Faizal"
                r"-WHL25080462-WH357-N027-1 sep\AMJ31-VGM,SI,Inv,PL.xls"),
}

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


# ---- naming and wiring (no Excel needed) -----------------------------------
check("filename follows {doc} - {exporter}{seq}.pdf",
      pdf_export.pdf_filename("SI", "AMJ", 24) == "SI - AMJ24.pdf")
check("sequence is not zero-padded in the filename",
      pdf_export.pdf_filename("VGM", "TSI", 1) == "VGM - TSI1.pdf")

check("four documents are exported (buyer invoice excluded)",
      [d for d, _ in pdf_export.DOCUMENTS]
      == ["SI", "VGM", "Inv BC", "PL"])
check("the buyer invoice is not exported",
      "Inv Buyer" not in [d for d, _ in pdf_export.DOCUMENTS])

e4 = next(s for s in WORKFLOW_STEPS if s[0] == "E4")
check("E4 is no longer a phase 2 step", e4[5] is False)
check("E4 has an export button", STEP_ACTIONS["E4"] == [("Ekspor PDF", "pdf", "")])
check("the export button does not blanket-complete the step",
      "pdf" in NON_COMPLETING_ACTION_KINDS)

check("PDF subfolder is the one file setup already creates",
      pdf_export.target_dir(Path("x")).name == "PDF")

# A missing workbook is reported, not raised.
missing = pdf_export.export_shipment(
    Path("nope.xlsx"), exporter_code="AMJ", sequence=1)
check("missing workbook reports instead of raising", not missing.ok
      and "tidak ditemukan" in missing.message())

# ---- real workbooks --------------------------------------------------------
available = {k: v for k, v in SOURCES.items() if v.is_file()}
if not available:
    print("\nSKIP - no live workbook reachable; naming checks only.")
else:
    for code, source in available.items():
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "shipment"
            folder.mkdir()
            book = folder / source.name
            shutil.copy2(source, book)

            result = pdf_export.export_shipment(
                book, exporter_code=code, sequence=7, folder=folder)

            print(f"\n{code}: {result.message()}")
            check(f"{code} export succeeded", result.ok)
            check(f"{code} nothing failed {result.failed or ''}",
                  not result.failed)
            check(f"{code} every document sheet was found {result.missing or ''}",
                  not result.missing)

            pdf_dir = folder / "PDF"
            names = sorted(p.name for p in pdf_dir.glob("*.pdf"))
            check(f"{code} four PDFs written {names}", len(names) == 4)
            check(f"{code} named as asked",
                  f"SI - {code}7.pdf" in names and f"PL - {code}7.pdf" in names)
            check(f"{code} the customs invoice is exported",
                  f"Inv BC - {code}7.pdf" in names)
            check(f"{code} the buyer invoice is NOT exported",
                  f"Inv Buyer - {code}7.pdf" not in names)
            check(f"{code} PDFs are not empty",
                  all(p.stat().st_size > 1000 for p in pdf_dir.glob("*.pdf")))
            check(f"{code} files really are PDFs",
                  all(p.read_bytes()[:4] == b"%PDF"
                      for p in pdf_dir.glob("*.pdf")))

            # Re-exporting overwrites in place rather than piling up copies.
            again = pdf_export.export_shipment(
                book, exporter_code=code, sequence=7, folder=folder)
            check(f"{code} re-export overwrites",
                  again.ok and len(list(pdf_dir.glob("*.pdf"))) == 4)

            # The workbook itself must come back untouched.
            check(f"{code} workbook is not modified by exporting",
                  book.stat().st_size == source.stat().st_size)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("PDF export OK - all checks passed.")
