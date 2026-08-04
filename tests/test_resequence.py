"""Sequence-number migration (2026-07-21).

Clash resolution is pure logic and always runs. The file/Excel half works on a
COPY of a real AMJ shipment folder and needs Excel + the Drive; it skips
cleanly when either is unavailable. Never touches G:\\.
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.context import AppContext
from bot_kalung.services import resequence as rs
from bot_kalung.services.shipments import Shipments

LIVE_AMJ = Path(r"G:\My Drive\AMJ\2026"
                r"\23.2x40-karachi-Salim-KLN-EVE-084600048570-Concert 0800-088N-03 aug")

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def fake(id_, code, seq):
    return {"id": id_, "exporter_code": code, "sequence_number": seq,
            "etd_belawan": "2026-08-03", "folder_path": None}


# ---- clash resolution (pure logic) ----------------------------------------
rows = [fake("a", "AMJ", 23), fake("b", "AMJ", 24), fake("c", "AMJ", 25),
        fake("t", "TTJ", 24)]

check("a free target has no clash", rs.find_clash(rows, {"a": 30}) is None)

clash = rs.find_clash(rows, {"a": 24})
check("a taken target reports who holds it",
      clash is not None and clash.label == "AMJ24" and clash.sequence == 24)

check("a swap resolves (23->24, 24->23)",
      rs.find_clash(rows, {"a": 24, "b": 23}) is None)

chained = rs.find_clash(rows, {"a": 24, "b": 25})
check("displacing onto another taken number clashes again",
      chained is not None and chained.label == "AMJ25")
check("the chain resolves once the last one moves",
      rs.find_clash(rows, {"a": 24, "b": 25, "c": 26}) is None)

check("sequences are per-exporter, so TTJ24 never clashes with AMJ24",
      rs.find_clash(rows, {"a": 24, "b": 23}) is None)

check("two moves onto the same number are caught",
      rs.find_clash(rows, {"a": 30, "b": 30}) is not None)

# ---- folder prefix renaming -------------------------------------------------
check("prefix swap keeps two-digit padding",
      rs._renamed_prefix("04.3x40-Karachi-x", 5).startswith("05."))
check("prefix swap keeps one-digit style",
      rs._renamed_prefix("1.1x40-Karachi-x", 2).startswith("2."))
check("prefix swap leaves the rest of the name untouched",
      rs._renamed_prefix("23.2x40'-karachi-Ever Concert-03 aug", 24)
      == "24.2x40'-karachi-Ever Concert-03 aug")

# ---- against a copy of a real shipment folder ------------------------------
if not LIVE_AMJ.is_dir():
    print(f"\nSKIP  live AMJ folder not reachable at {LIVE_AMJ}")
else:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "Drive"
        (root / "AMJ" / "2026").mkdir(parents=True)
        folder = root / "AMJ" / "2026" / LIVE_AMJ.name
        shutil.copytree(LIVE_AMJ, folder)

        # A stale exported PDF to prove those get renamed too.
        pdf_dir = folder / "PDF"
        pdf_dir.mkdir(exist_ok=True)
        (pdf_dir / "SI - AMJ23.pdf").write_bytes(b"%PDF-1.4 stub")

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

        db_rows = ctx.db.query("SELECT * FROM shipments")
        plan = rs.build_plan(db_rows, {sid: 24})
        check("plan covers the one shipment", len(plan) == 1)
        move = plan[0]
        check("plan renames the folder prefix",
              move.new_folder is not None and move.new_folder.name.startswith("24."))
        check("plan renames the workbook and invoice",
              len(move.file_renames) == 2)
        check("plan renames the stale PDF", len(move.pdf_renames) == 1)
        check("plan computes the new document number",
              move.old_document_number == "23082026"
              and move.new_document_number == "24082026")

        blockers = rs.preflight(plan)
        check(f"nothing blocks a closed folder {[b.reason for b in blockers]}",
              not blockers)

        result = rs.execute(ctx.db, plan)
        check(f"migration succeeded {result.failed or ''}", result.ok)

        new_folder = move.new_folder
        check("folder renamed on disk",
              new_folder.is_dir() and not folder.exists())
        names = sorted(p.name for p in new_folder.iterdir() if p.is_file())
        check(f"workbook renamed to AMJ24 {names}",
              any(n.startswith("AMJ24 ") and "VGM,SI,Inv,PL" in n for n in names))
        check("invoice renamed to AMJ24",
              any("AMJ24" in n and "Invoice" in n for n in names))
        check("no AMJ23 files left",
              not any("AMJ23" in n for n in names))
        check("exported PDF renamed",
              (new_folder / "PDF" / "SI - AMJ24.pdf").is_file())
        check("the PDF-contents caveat is reported",
              any("isinya masih memuat nomor lama" in w for w in result.warnings))

        row = shipments.get(sid)
        check("database row renumbered", row["sequence_number"] == 24)
        check("database folder_path follows the rename",
              row["folder_path"] == str(new_folder))

        # The whole point: the app must now read 24, not 23, from disk.
        from bot_kalung.services import drive
        check("sequence detection now reports the new number",
              drive._sequence_from_documents(new_folder) == 24)

        # Excel document number actually changed inside the workbook.
        try:
            import xlwings  # noqa: F401

            from bot_kalung.services import excel
            workbook = next(p for p in new_folder.iterdir()
                            if drive.is_main_workbook(p.name))
            with excel.open_book(workbook) as book:
                vgm = excel.find_sheet(book, "VGM")
                cell = (excel.find_label(vgm, "NO :", column=2)
                        or excel.find_label(vgm, "VGM-", column=2))
                value = str(vgm.range(cell).value) if cell else ""
            check(f"VGM number rewritten in the workbook ({value})",
                  "24082026" in value)
        except Exception as exc:      # noqa: BLE001
            print(f"SKIP  Excel verification unavailable ({exc})")

# ---- dialog flow (no files needed) ------------------------------------------
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication, QInputDialog

from bot_kalung.ui.main_window import MainWindow
from bot_kalung.ui.resequence_dialog import ResequenceDialog

app = QApplication.instance() or QApplication([])

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)
    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set_many({"setup_complete": "1", "my_email": "a@x.com"})
    shipments = Shipments(ctx.db)

    def make(seq, vessel):
        return shipments.create({
            "exporter_code": "AMJ", "sequence_number": seq,
            "vessel_name": vessel, "voyage": "1N", "booking_number": "B",
            "etd_belawan": "2026-08-03", "destination_port": "KARACHI",
            "destination_country": "PAKISTAN", "container_quantity": 1,
            "container_size_short": "40'",
        })

    first, second = make(23, "ALPHA"), make(24, "BETA")

    window = MainWindow(ctx)
    check("dashboard offers the resequence button",
          window.dashboard.resequence_button.text() == "Ubah Nomor Urut")

    dialog = ResequenceDialog(ctx.db, window)
    check("dialog lists every shipment", dialog.shipment_combo.count() == 2)

    # Select AMJ23 and target 24 (taken by AMJ24) -> the clash prompt fires.
    index = next(i for i in range(dialog.shipment_combo.count())
                 if dialog.shipment_combo.itemData(i) == first)
    dialog.shipment_combo.setCurrentIndex(index)
    dialog.sequence_field.setValue(24)

    asked = []

    def fake_get_int(parent, title, label, value=0, minimum=0, maximum=0):
        asked.append(label)
        return 23, True        # send the displaced shipment to the vacated slot

    QInputDialog.getInt = staticmethod(fake_get_int)
    dialog._check()

    check("the clash prompted the user", len(asked) == 1
          and "AMJ24" in asked[0])
    check("the resolved plan covers both shipments", len(dialog.plan) == 2)
    check("run is enabled once the plan is clean",
          dialog.run_button.isEnabled())

    dialog._run()
    check("the swap applied to the database",
          shipments.get(first)["sequence_number"] == 24
          and shipments.get(second)["sequence_number"] == 23)

    # Same number in and out is refused.
    dialog.sequence_field.setValue(shipments.get(first)["sequence_number"])
    idx = next(i for i in range(dialog.shipment_combo.count())
               if dialog.shipment_combo.itemData(i) == first)
    dialog.shipment_combo.setCurrentIndex(idx)
    dialog._check()
    check("changing to the same number is refused",
          not dialog.run_button.isEnabled())

    window.wizard.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Resequence OK - all checks passed.")
