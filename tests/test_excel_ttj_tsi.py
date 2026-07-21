"""Excel pre-fill against COPIES of the live TTJ and THREESTAR workbooks.

Both are .xlsx and, unlike NIT, do link their SI and P.List rows back to the
VGM container block — so all three sheets adjust. Both also sit on single-digit
sequences, which is where the document-number padding matters.

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

from bot_kalung.services import excel, naming

CASES = {
    "TTJ": {
        "path": Path(r"G:\My Drive\TASHA-HUSSAIN-MAJEED\2026 Tasha"
                     r"\04.3x40-Karachi-Sunli-OOCL2331585571-Integra179E"
                     r"\TTJ04-Karachi-VGM,SI,INV,PL.xlsx"),
        "sequence": 5,
        "etd": date(2026, 9, 4),
        "expected_number": "05092026",
        "start_rows": 3,
    },
    "TSI": {
        "path": Path(r"G:\My Drive\Three star-waleed"
                     r"\1.1x40-Karachi-GAN-OOCL2331904123-Integra182E-2 aug"
                     r"\TSI01-Karachi-VGM,SI,INV,PL.xlsx"),
        "sequence": 2,
        "etd": date(2026, 9, 4),
        "expected_number": "02092026",
        "start_rows": 1,
    },
}

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


available = {c: d for c, d in CASES.items() if d["path"].is_file()}
if not available:
    print("SKIP  neither TTJ nor THREESTAR workbook is reachable")
    sys.exit(0)

app = xw.App(visible=False, add_book=False)
app.display_alerts = False

try:
    for code, case in available.items():
        print(f"\n--- {code} ---")
        tmp = Path(tempfile.mkdtemp())
        copy = tmp / case["path"].name
        shutil.copy2(case["path"], copy)
        book = app.books.open(str(copy), update_links=False)
        try:
            vgm = excel.find_sheet(book, "VGM")
            si = excel.find_sheet(book, "SI")
            plist = excel.find_sheet(book, "P.List")
            check(f"{code}: all three sheets resolve",
                  None not in (vgm, si, plist))

            _, before = excel.vgm_container_rows(vgm)
            check(f"{code}: starts with {case['start_rows']} container row(s)",
                  len(before) == case["start_rows"])
            check(f"{code}: SI rows are linked to VGM",
                  len(excel.container_rows(si, before)) == case["start_rows"])
            check(f"{code}: P.List rows are linked to VGM",
                  len(excel.container_rows(plist, before)) == case["start_rows"])

            report = excel.prefill_workbook(
                book, sequence=case["sequence"], etd=case["etd"],
                booking_number="TESTBK123", vessel_name="TEST VESSEL",
                voyage="900W", container_quantity=5, size_short="40'")

            number = case["expected_number"]
            check(f"{code}: VGM number zero-padded -> VGM-{number}",
                  vgm.range("B3").value == f"NO : VGM-{number}")
            check(f"{code}: document number helper agrees",
                  naming.document_number(case["sequence"], case["etd"]) == number)
            check(f"{code}: VGM date written as text",
                  vgm.range("E5").value == "SEPTEMBER 2026")
            check(f"{code}: booking written",
                  vgm.range("E6").value == "TESTBK123")
            check(f"{code}: vessel written",
                  vgm.range("E7").value == "TEST VESSEL 900W")

            _, after = excel.vgm_container_rows(vgm)
            check(f"{code}: VGM grew to 5 rows", len(after) == 5)
            check(f"{code}: rows numbered 1..5",
                  [vgm.range((r, 2)).value for r in after]
                  == [float(i) for i in range(1, 6)])
            check(f"{code}: container type kept from the workbook",
                  {vgm.range((r, 3)).value for r in after} == {"40'HC"})
            check(f"{code}: container/seal/tare left blank",
                  all(vgm.range((r, c)).value in (None, "")
                      for r in after for c in (4, 5, 8)))

            check(f"{code}: SI grew to 5 linked rows",
                  len(excel.container_rows(si, after)) == 5)
            check(f"{code}: P.List grew to 5 linked rows",
                  len(excel.container_rows(plist, after)) == 5)

            etd_cell = excel.find_label(si, "ETD", exact=True)
            target = excel.value_column_after(si, *etd_cell)
            check(f"{code}: SI ETD written",
                  si.range((etd_cell[0], target)).value == "04 SEPTEMBER 2026")

            party = excel.find_label(si, "Party", exact=True)
            party_target = excel.value_column_after(si, *party)
            check(f"{code}: SI party reflects the quantity",
                  si.range((party[0], party_target)).value == "5 X 40'HC")

            check(f"{code}: SI title carries the padded number",
                  si.range("B2").value == f"SHIPPING INSTRUCTION - {number}")
            check(f"{code}: nothing reported as unadjustable",
                  not [w for w in report.warnings if "manual" in w])

            book.save()
        finally:
            book.close()
            shutil.rmtree(tmp, ignore_errors=True)
finally:
    app.quit()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Excel TTJ/THREESTAR OK - all checks passed.")
