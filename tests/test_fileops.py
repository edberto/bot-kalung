"""Naming derivations that survived the refactor + folder deletion.

Folder/document construction was removed; this covers the document-number/date
helpers still used by resequence & etd_change, and the Recycle-Bin delete.
"""

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.services import naming
from bot_kalung.services.fileops import delete_shipment_folder

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


# ---- quarantine + document number/date helpers (kept) --------------------
check("quarantine matches case-insensitively",
      naming.is_quarantine_required("pakistan", ["PAKISTAN"]))
check("quarantine excludes others",
      not naming.is_quarantine_required("Singapore", ["PAKISTAN"]))
check("quarantine tolerates None", not naming.is_quarantine_required(None, ["PAKISTAN"]))

# ETD 03 Aug is early in the month -> same month.
check("vgm month stays for an early ETD",
      naming.vgm_date_month(date(2026, 8, 3)) == "AUGUST 2026")
check("vgm month rolls on the 29th", naming.vgm_date_month(date(2026, 8, 29)) == "SEPTEMBER 2026")
check("vgm month holds on the 28th", naming.vgm_date_month(date(2026, 8, 28)) == "AUGUST 2026")
check("vgm month rolls across the year",
      naming.vgm_date_month(date(2026, 12, 31)) == "JANUARY 2027")
check("vgm month respects a short February",
      naming.vgm_date_month(date(2027, 2, 26)) == "MARCH 2027")

check("document number matches the live AMJ23 file",
      naming.document_number(23, date(2026, 8, 3)) == "23082026")
check("vgm number matches the live AMJ23 file",
      naming.vgm_number(23, date(2026, 8, 3)) == "VGM-23082026")
check("si title matches the live AMJ23 file",
      naming.si_title(23, date(2026, 8, 3)) == "SHIPPING INSTRUCTION - 23082026")
check("etd long form matches the live AMJ23 SI cell",
      naming.etd_long(date(2026, 8, 3)) == "03 AUGUST 2026")

check("single-digit sequence is zero-padded",
      naming.document_number(9, date(2026, 9, 4)) == "09092026")
check("three-digit sequences are not truncated",
      naming.document_number(123, date(2026, 8, 3)) == "123082026")

check("iso date parses", naming.parse_iso_date("2026-08-03") == date(2026, 8, 3))
check("iso date rejects junk", naming.parse_iso_date("not a date") is None)
check("iso date rejects an impossible day", naming.parse_iso_date("2026-02-30") is None)

# ---- deleting a shipment folder (to the Recycle Bin) ---------------------
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)

    live = tmp / "24.2x40-karachi"
    (live / "Draf").mkdir(parents=True)
    (live / "AMJ24-VGM,SI,Inv,PL.xls").write_text("x", encoding="utf-8")
    result = delete_shipment_folder(live)
    check("folder delete reports success", result.removed and result.existed)
    check("folder delete went to the Recycle Bin", result.recycled)
    check("folder is actually gone", not live.exists())

    gone = delete_shipment_folder(tmp / "never-existed")
    check("missing folder is a no-op success",
          gone.removed and not gone.existed and not gone.recycled)

    none = delete_shipment_folder(None)
    check("empty folder path is handled", none.removed and not none.existed)

    stray = tmp / "not-a-folder.txt"
    stray.write_text("x", encoding="utf-8")
    bad = delete_shipment_folder(stray)
    check("a plain file is refused", not bad.removed and bad.existed
          and stray.exists())

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("File ops OK - all checks passed.")
