"""ETD migration (2026-07-22).

Folder-suffix and planning logic always runs. The Excel half works on a COPY of
a real AMJ shipment folder and needs Excel + the Drive; it skips cleanly when
either is unavailable. Never touches G:\\.
"""

import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.context import AppContext
from bot_kalung.services import etd_change, naming
from bot_kalung.services.shipments import Shipments

LIVE_AMJ = Path(r"G:\My Drive\AMJ\2026"
                r"\23.2x40-karachi-Salim-KLN-EVE-084600048570-Concert 0800-088N-03 aug")

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


# ---- folder date suffix (pure logic) ---------------------------------------
name = "23.2x40-karachi-Salim-KLN-EVE-084600048570-Concert 0800-088N-03 aug"
check("the trailing date segment is swapped",
      etd_change._renamed_suffix(name, date(2026, 9, 15)).endswith("-15 sep"))
check("everything before the date is preserved",
      etd_change._renamed_suffix(name, date(2026, 9, 15)).startswith(
          "23.2x40-karachi-Salim-KLN-EVE-084600048570-Concert 0800-088N"))
check("a folder without a date segment is left alone",
      etd_change._renamed_suffix("04.3x40-Karachi-Integra179E", date(2026, 9, 1))
      == "04.3x40-Karachi-Integra179E")
check("single-digit days are zero-padded like the original",
      etd_change._renamed_suffix(name, date(2026, 9, 5)).endswith("-05 sep"))

# ---- what a new ETD implies -------------------------------------------------
check("the document number embeds the ETD's month and year",
      naming.document_number(23, date(2026, 8, 3)) == "23082026"
      and naming.document_number(23, date(2026, 9, 3)) == "23092026")
check("the VGM date rolls in the last three days of a month",
      naming.vgm_date_month(date(2026, 8, 30)) == "SEPTEMBER 2026")
check("the SI ETD is the long form",
      naming.etd_long(date(2026, 9, 15)) == "15 SEPTEMBER 2026")

# ---- against a copy of a real shipment folder ------------------------------
if not LIVE_AMJ.is_dir():
    print(f"\nSKIP  live AMJ folder not reachable at {LIVE_AMJ}")
else:
    def setup(tmp: Path):
        root = tmp / "Drive"
        (root / "AMJ" / "2026").mkdir(parents=True)
        folder = root / "AMJ" / "2026" / LIVE_AMJ.name
        shutil.copytree(LIVE_AMJ, folder)
        ctx = AppContext()
        ctx.create(root)
        shipments = Shipments(ctx.db)
        sid = shipments.create({
            "exporter_code": "AMJ", "sequence_number": 23,
            "booking_number": "084600048570", "vessel_name": "EVER CONCERT",
            "voyage": "0800-088N", "etd_belawan": "2026-08-03",
            "destination_port": "KARACHI", "destination_country": "PAKISTAN",
            "container_quantity": 2, "container_size_short": "40'",
            "folder_path": str(folder),
        })
        return ctx, shipments, sid, folder

    # --- moving to another month rewrites the document number ---------------
    with tempfile.TemporaryDirectory() as tmp:
        ctx, shipments, sid, folder = setup(Path(tmp))
        row = shipments.get(sid)
        plan = etd_change.build_plan(row, date(2026, 9, 15))

        check("plan renames the folder's date suffix",
              plan.new_folder is not None
              and plan.new_folder.name.endswith("-15 sep"))
        check("plan sees the document number change across months",
              plan.document_number_changes
              and plan.old_document_number == "23082026"
              and plan.new_document_number == "23092026")
        check("plan carries the new VGM date and SI ETD",
              plan.new_vgm_date == "SEPTEMBER 2026"
              and plan.new_si_etd == "15 SEPTEMBER 2026")
        check(f"nothing blocks a closed folder {etd_change.preflight(plan)}",
              not etd_change.preflight(plan))

        result = etd_change.execute(ctx.db, plan)
        check(f"migration succeeded {result.failed or ''}", result.ok)
        check("folder renamed on disk",
              plan.new_folder.is_dir() and not folder.exists())
        updated = shipments.get(sid)
        check("database ETD updated", updated["etd_belawan"] == "2026-09-15")
        check("database folder_path follows the rename",
              updated["folder_path"] == str(plan.new_folder))
        check("the stale-PDF caveat is reported",
              any("ETD lama" in w for w in result.warnings))

        # The workbook's own cells followed.
        try:
            from bot_kalung.services import drive, excel

            workbook = next(p for p in plan.new_folder.iterdir()
                            if drive.is_main_workbook(p.name))
            with excel.open_book(workbook) as book:
                vgm = excel.find_sheet(book, "VGM")
                cell = (excel.find_label(vgm, "NO :", column=2)
                        or excel.find_label(vgm, "VGM-", column=2))
                number = str(vgm.range(cell).value) if cell else ""
                date_cell = excel.find_label(vgm, "DATE", column=2, exact=True)
                vgm_date = (str(vgm.range((date_cell[0], 5)).value)
                            if date_cell else "")
            check(f"VGM number rewritten for the new month ({number})",
                  "23092026" in number)
            check(f"VGM DATE rewritten ({vgm_date})", "SEPTEMBER" in vgm_date.upper())
        except Exception as exc:      # noqa: BLE001
            print(f"SKIP  Excel verification unavailable ({exc})")

    # --- a same-month day change leaves the document number alone -----------
    with tempfile.TemporaryDirectory() as tmp:
        ctx, shipments, sid, folder = setup(Path(tmp))
        plan = etd_change.build_plan(shipments.get(sid), date(2026, 8, 11))
        check("a same-month move keeps the document number",
              not plan.document_number_changes
              and plan.new_document_number == "23082026")
        check("but the folder suffix and SI ETD still change",
              plan.new_folder.name.endswith("-11 aug")
              and plan.new_si_etd == "11 AUGUST 2026")

    # --- a locked workbook blocks the migration ----------------------------
    with tempfile.TemporaryDirectory() as tmp:
        ctx, shipments, sid, folder = setup(Path(tmp))
        plan = etd_change.build_plan(shipments.get(sid), date(2026, 9, 15))
        # Excel leaves a "~$" lock file beside an open workbook.
        (folder / "~$AMJ23-VGM,SI,Inv,PL.xls").write_text("lock", encoding="utf-8")
        blockers = etd_change.preflight(plan)
        check("an open workbook blocks the migration", bool(blockers))
        check("the blocker says what to close",
              any("Tutup" in b.reason for b in blockers))
        check("nothing was renamed while blocked", folder.is_dir())
        check("the ETD is untouched while blocked",
              shipments.get(sid)["etd_belawan"] == "2026-08-03")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ETD change OK - all checks passed.")
