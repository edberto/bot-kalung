"""Excel pre-fill against a COPY of the live AMJ workbook.

Requires Microsoft Excel. Never touches G:\\ — the workbook is copied to a temp
directory and all writes happen there. Dumps the resulting cells so the output
can be eyeballed against the PRD.
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
from bot_kalung.services.excel import PrefillReport

LIVE = Path(r"G:\My Drive\AMJ\2026"
            r"\23.2x40-karachi-Salim-KLN-EVE-084600048570-Concert 0800-088N-03 aug"
            r"\AMJ23-VGM,SI,Inv,PL.xls")

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


if not LIVE.is_file():
    print(f"SKIP  live AMJ workbook not reachable at {LIVE}")
    sys.exit(0)

tmp = Path(tempfile.mkdtemp())
copy = tmp / "AMJ24-VGM,SI,Inv,PL.xls"
shutil.copy2(LIVE, copy)

ETD = date(2026, 8, 3)
app = xw.App(visible=False, add_book=False)
app.display_alerts = False

try:
    book = app.books.open(str(copy), update_links=False)
    report = PrefillReport()
    try:
        # --- lookup helpers -------------------------------------------------
        check("VGM sheet found by prefix", excel.find_sheet(book, "VGM") is not None)
        check("SI sheet found despite trailing space",
              excel.find_sheet(book, "SI") is not None)
        check("P.List sheet found", excel.find_sheet(book, "P.List") is not None)
        check("missing sheet returns None", excel.find_sheet(book, "NOPE") is None)

        si = excel.find_sheet(book, "SI")
        last_row, last_col = excel.content_extent(si)
        check(f"SI content extent stops at the real content (row {last_row}, "
              f"not used_range 122)", last_row <= 40)

        vgm = excel.find_sheet(book, "VGM")
        _, vgm_rows = excel.vgm_container_rows(vgm)
        check(f"VGM container rows are 16/17 -> {vgm_rows}", vgm_rows == [16, 17])

        rows = excel.container_rows(si, vgm_rows)
        check(f"SI container rows detected as interleaved 16/18 -> {rows}",
              rows == [16, 18])
        check("booking/feeder cross-references are NOT counted as containers",
              4 not in rows and 23 not in rows)

        si_title_formula = si.range("B2").formula
        check("SI title is a formula in the template",
              isinstance(si_title_formula, str) and si_title_formula.startswith("="))

        # --- pre-fill, quantity unchanged (2) --------------------------------
        excel.prefill_workbook(
            book, sequence=24, etd=ETD, booking_number="084600048570",
            vessel_name="EVER CONCERT", voyage="0800-088N",
            container_quantity=2, size_short="40'", report=report)
        check("VGM number written",
              vgm.range("B8").value == "NO : VGM-24082026")
        check("VGM date written as text, not coerced to a datetime",
              vgm.range("E10").value == "AUGUST 2026")
        check("VGM date is red", vgm.range("E10").api.Font.Color == 255)
        check("VGM booking keeps its leading zero",
              vgm.range("E11").value == "084600048570")
        check("VGM vessel written",
              vgm.range("E12").value == "EVER CONCERT 0800-088N")
        check("VGM container rows still 2",
              vgm.range("B16").value == 1 and vgm.range("B17").value == 2)
        check("VGM size written", vgm.range("C16").value == "40'HQ")
        check("VGM container/seal/tare left blank",
              all(vgm.range(a).value in (None, "")
                  for a in ("D16", "E16", "H16", "D17", "E17", "H17")))

        # The title must still be the formula, and must now render the new number.
        check("SI title formula preserved, not overwritten",
              si.range("B2").formula == si_title_formula)
        check("SI title renders the new number via its formula",
              si.range("B2").value == "SHIPPING INSTRUCTION - 24082026")
        check("SI ETD written", si.range("C24").value == "03 AUGUST 2026")
        check("SI ETD is red", si.range("C24").api.Font.Color == 255)
        check("SI cross-references to VGM preserved",
              "VGM" in str(si.range("G16").formula).upper())
        check("SI rows 18-23 untouched (nothing deleted)",
              str(si.range("B20").value).replace(" ", "").upper() == "TOTAL")

        party = excel.find_label(si, "Party", exact=True)
        check("SI party cell reflects quantity x size",
              party is not None
              and si.range((party[0], party[1] + 1)).value == "2 X 40'HC")

        # The LOLO reference table was dropped from the requirements on
        # 2026-07-20; nothing should write an "Indra" block to the SI.
        check("no LOLO table is written to the SI",
              excel.find_label(si, "Seal & lolo mty") is None)
        check("no LOLO named range is created",
              not [n for n in book.names
                   if "LOLO" in n.name.split("!")[-1].upper()])

        # --- quantity change 2 -> 3 -------------------------------------------
        excel.prefill_workbook(
            book, sequence=24, etd=ETD, booking_number="084600048570",
            vessel_name="EVER CONCERT", voyage="0800-088N",
            container_quantity=3, size_short="40'", report=report)

        _, grown = excel.vgm_container_rows(vgm)
        check("VGM grew to 3 container rows", len(grown) == 3)
        check("VGM third row numbered and sized",
              vgm.range((grown[2], 2)).value == 3
              and vgm.range((grown[2], 3)).value == "40'HQ")
        si_rows = excel.container_rows(si, grown)
        print(f"      -> VGM rows {grown}, SI container rows {si_rows}")
        for r in range(14, 24):
            cells = []
            for c in range(2, 9):
                cell = si.range((r, c))
                f = cell.formula
                shown = f if isinstance(f, str) and f.startswith("=") else cell.value
                if shown not in (None, ""):
                    cells.append(f"{xw.utils.col_name(c)}{r}={str(shown)[:24]!r}")
            print(f"         row {r}: {' | '.join(cells) if cells else '(empty)'}")
        check("SI grew to 3 container rows", len(si_rows) == 3)

        party = excel.find_label(si, "Party", exact=True)
        check("SI party cell updated to the new quantity",
              party is not None
              and si.range((party[0], party[1] + 1)).value == "3 X 40'HC")
        check("container weights left untouched", si.range("E16").value == 23000)

        total = excel.find_label(si, "T O T A L")
        if total:
            formula = str(si.range((total[0], 4)).formula)
            print(f"      -> SI total formula after growth: {formula}")
            check("SI total formula still a SUM", "SUM" in formula.upper())

        print("\n--- changes ---")
        for line in report.changes:
            print(f"  {line}")
        if report.skips:
            print("--- skipped (formula-backed) ---")
            for line in report.skips:
                print(f"  = {line}")
        if report.warnings:
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
print("Excel AMJ OK - all checks passed.")
