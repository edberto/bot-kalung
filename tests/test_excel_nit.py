"""Excel pre-fill against a COPY of the live NIT workbook.

NIT differs from AMJ in three ways this covers:
  * SI labels are merged across two columns, so the value sits two columns right
  * the container type is written "40'HC", not AMJ's "40'HQ"
  * the SI and P.List describe cargo in prose, with no per-container rows tied
    to the VGM sheet

Requires Microsoft Excel. Never touches G:\\.
"""

import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

import xlwings as xw

from bot_kalung.services import excel

LIVE = Path(r"G:\My Drive\NMEHMOOD & CV.Hassan\2026"
            r"\15.10x40-Chennai-Maruti-GAN-T22854-Reya123N-25 july"
            r"\NIT15-CHENNAI-VGM,SI,INV,PL.xlsx")

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


if not LIVE.is_file():
    print(f"SKIP  live NIT workbook not reachable at {LIVE}")
    sys.exit(0)

tmp = Path(tempfile.mkdtemp())
copy = tmp / "NIT16-CHENNAI-VGM,SI,INV,PL.xlsx"
shutil.copy2(LIVE, copy)

ETD = date(2026, 8, 14)
app = xw.App(visible=False, add_book=False)
app.display_alerts = False

try:
    book = app.books.open(str(copy), update_links=False)
    try:
        # ---- sheet resolution despite the exporter's own naming -------------
        vgm = excel.find_sheet(book, "VGM")
        si = excel.find_sheet(book, "SI")
        plist = excel.find_sheet(book, "P.List")
        check("VGM sheet found", vgm is not None)
        check("SI sheet found despite the 'benar' suffix",
              si is not None and si.name.strip() == "SI  benar")
        check("P.List found despite the 'nipah gabung' suffix",
              plist is not None and "nipah" in plist.name)

        header, rows_before = excel.vgm_container_rows(vgm)
        check(f"VGM container block found at row {header}", header == 10)
        check("ten container rows to start", len(rows_before) == 10)

        # ---- merged labels --------------------------------------------------
        etd_label = excel.find_label(si, "ETD", exact=True)
        check("ETD label found in column G", etd_label == (20, 7))
        check("merged label resolves its value two columns right",
              excel.value_column_after(si, 20, 7) == 9)   # I, not H

        party_label = excel.find_label(si, "Party", exact=True)
        check("Party label found", party_label == (18, 7))
        check("Party value column also skips the merge",
              excel.value_column_after(si, 18, 7) == 9)

        # ---- pre-fill, shrinking 10 -> 8 -------------------------------------
        report = excel.prefill_workbook(
            book, sequence=16, etd=ETD, booking_number="T99999",
            vessel_name="MTT REYA", voyage="26RY124N",
            container_quantity=8, size_short="40'")

        check("VGM number written", vgm.range("B3").value == "NO : VGM-16082026")
        check("VGM date written as text", vgm.range("E5").value == "AUGUST 2026")
        check("VGM booking written", vgm.range("E6").value == "T99999")
        check("VGM vessel written", vgm.range("E7").value == "MTT REYA 26RY124N")

        _, rows_after = excel.vgm_container_rows(vgm)
        check("container rows shrank to 8", len(rows_after) == 8)
        check("rows renumbered 1..8",
              [vgm.range((r, 2)).value for r in rows_after]
              == [float(i) for i in range(1, 9)])
        check("container type keeps this workbook's HC suffix",
              all(vgm.range((r, 3)).value == "40'HC" for r in rows_after))
        check("container/seal/tare cleared for the worker",
              all(vgm.range((r, c)).value in (None, "")
                  for r in rows_after for c in (4, 5, 8)))

        check("SI ETD written to the merged label's value cell",
              si.range("I20").value == "14 AUGUST 2026")
        check("H20 left untouched inside the merge",
              si.range("H20").value in (None, ""))
        check("SI party reflects the new quantity",
              si.range("I18").value == "8 X 40'HC")
        check("SI title written", si.range("B2").value
              == "SHIPPING INSTRUCTION - 16082026")

        # ---- prose sheets are reported, not silently skipped -----------------
        check("SI reports that it has no linked container rows",
              any("SI" in w and "manual" in w for w in report.warnings))
        check("P.List reports the same",
              any("P.List" in w and "manual" in w for w in report.warnings))
        # The prose 'TOTAL' cell has drifted rows as the live workbook is
        # edited (D33 -> D36 by 2026-07-21), so locate it rather than pinning a
        # row. What matters is that the prose is still there and untouched.
        check("SI prose content left intact",
              any(si.range((r, 4)).value == "TOTAL" for r in range(28, 45)))

        # ---- growing back 8 -> 12 --------------------------------------------
        excel.prefill_workbook(
            book, sequence=16, etd=ETD, booking_number="T99999",
            vessel_name="MTT REYA", voyage="26RY124N",
            container_quantity=12, size_short="40'")
        _, grown = excel.vgm_container_rows(vgm)
        check("container rows grew to 12", len(grown) == 12)
        check("grown rows numbered through 12",
              vgm.range((grown[-1], 2)).value == 12.0)
        check("grown rows keep the HC suffix",
              vgm.range((grown[-1], 3)).value == "40'HC")

        print("\n--- changes ---")
        for line in report.changes:
            print(f"  {line}")
        print("--- warnings ---")
        for line in report.warnings:
            print(f"  ! {line}")

        book.save()
    finally:
        book.close()
finally:
    app.quit()
    print(f"\n(result workbook left at {copy})")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Excel NIT OK - all checks passed.")
