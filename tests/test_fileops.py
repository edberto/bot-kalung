"""Folder operations and naming, exercised against a copy of a real AMJ folder.

Nothing here touches G:\\ — the live source folder is copied into a temp dir
first and all operations run there.
"""

import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.services import naming
from bot_kalung.services.fileops import (
    FileOpError, create_shipment_folder, find_permit,
)

LIVE_AMJ = Path(r"G:\My Drive\AMJ\2026"
                r"\23.2x40-karachi-Salim-KLN-EVE-084600048570-Concert 0800-088N-03 aug")

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


# ---- naming / post-processing (PRD 8.3, 9.3) -----------------------------
check("size short strips HI-CUBE", naming.container_size_short("40' HI-CUBE") == "40'")
check("size short handles a bare 40'", naming.container_size_short("40'") == "40'")
check("size short synthesises a missing foot mark",
      naming.container_size_short("40 HC") == "40'")
check("size short tolerates None", naming.container_size_short(None) == "")

check("quarantine matches case-insensitively",
      naming.is_quarantine_required("pakistan", ["PAKISTAN"]))
check("quarantine excludes others",
      not naming.is_quarantine_required("Singapore", ["PAKISTAN"]))
check("quarantine tolerates None", not naming.is_quarantine_required(None, ["PAKISTAN"]))

# ETD 03 Aug is early in the month -> same month.
check("vgm month stays for an early ETD",
      naming.vgm_date_month(date(2026, 8, 3)) == "AUGUST 2026")
# Aug has 31 days, so the last three days are 29/30/31.
check("vgm month rolls on the 29th", naming.vgm_date_month(date(2026, 8, 29)) == "SEPTEMBER 2026")
check("vgm month holds on the 28th", naming.vgm_date_month(date(2026, 8, 28)) == "AUGUST 2026")
check("vgm month rolls across the year",
      naming.vgm_date_month(date(2026, 12, 31)) == "JANUARY 2027")
# February 2027 has 28 days -> last three are 26/27/28.
check("vgm month respects a short February",
      naming.vgm_date_month(date(2027, 2, 26)) == "MARCH 2027")

# Matches the live AMJ23 workbook: "NO : VGM-23082026".
check("document number matches the live AMJ23 file",
      naming.document_number(23, date(2026, 8, 3)) == "23082026")
check("vgm number matches the live AMJ23 file",
      naming.vgm_number(23, date(2026, 8, 3)) == "VGM-23082026")
check("si title matches the live AMJ23 file",
      naming.si_title(23, date(2026, 8, 3)) == "SHIPPING INSTRUCTION - 23082026")
check("etd long form matches the live AMJ23 SI cell",
      naming.etd_long(date(2026, 8, 3)) == "03 AUGUST 2026")

# Every live workbook zero-pads the sequence to two digits.
check("single-digit sequence is zero-padded",
      naming.document_number(9, date(2026, 9, 4)) == "09092026")
check("TTJ04's real number reproduced",
      naming.document_number(4, date(2026, 7, 7)) == "04072026")
check("TSI01's padding reproduced",
      naming.document_number(1, date(2026, 7, 1)) == "01072026")
# The live TSI01 workbook reads "01072026" (July) but its DO gives an August
# ETD, because it was prepared in July. The month comes from the DO's ETD, so
# the app writes August and deliberately differs from that file (user, 2026-07-20).
check("month comes from the ETD, not the month of preparation",
      naming.document_number(1, date(2026, 8, 4)) == "01082026")
check("three-digit sequences are not truncated",
      naming.document_number(123, date(2026, 8, 3)) == "123082026")

check("vessel title-cases", naming.vessel_title("EVER CONCERT") == "Ever Concert")
check("folder name follows PRD 9.3",
      naming.folder_name(sequence=23, container_quantity=2, size_short="40'",
                         destination_port="KARACHI", booking_number="084600048570",
                         vessel_name="EVER CONCERT", voyage="0800-088N",
                         etd=date(2026, 8, 3))
      == "23.2x40'-karachi-084600048570-Ever Concert 0800-088N-03 aug")

# Folder padding follows the exporter's existing folders.
check("folder keeps TTJ's two-digit padding",
      naming.folder_name(sequence=5, container_quantity=3, size_short="40'",
                         destination_port="KARACHI", booking_number="2331585571",
                         vessel_name="INTEGRA", voyage="179E",
                         etd=date(2026, 5, 2), sequence_width=2)
      .startswith("05."))
check("folder stays unpadded where the exporter is",
      naming.folder_name(sequence=2, container_quantity=1, size_short="40'",
                         destination_port="KARACHI", booking_number="2331904123",
                         vessel_name="INTEGRA", voyage="182E",
                         etd=date(2026, 8, 2), sequence_width=1)
      .startswith("2."))
check("padding never truncates a wider sequence",
      naming.folder_name(sequence=123, container_quantity=1, size_short="40'",
                         destination_port="X", booking_number="1", vessel_name="V",
                         voyage="1", etd=date(2026, 8, 2), sequence_width=2)
      .startswith("123."))

check("iso date parses", naming.parse_iso_date("2026-08-03") == date(2026, 8, 3))
check("iso date rejects junk", naming.parse_iso_date("not a date") is None)
check("iso date rejects an impossible day", naming.parse_iso_date("2026-02-30") is None)

# ---- folder operations against a copy of the live AMJ folder -------------
if not LIVE_AMJ.is_dir():
    print(f"\nSKIP  live AMJ folder not reachable at {LIVE_AMJ}")
else:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        source = tmp / "2026" / LIVE_AMJ.name
        source.parent.mkdir(parents=True)
        shutil.copytree(LIVE_AMJ, source)
        print(f"\n(copied live AMJ23 folder: {len(list(source.iterdir()))} entries)")

        before_files = sorted(p.name for p in source.iterdir() if p.is_file())
        print(f" source files: {before_files}")

        do_pdf = next(p for p in source.iterdir()
                      if p.suffix.lower() == ".pdf" and "ETD BLW" in p.name.upper())

        target_name = naming.folder_name(
            sequence=24, container_quantity=2, size_short="40'",
            destination_port="KARACHI", booking_number="084600048570",
            vessel_name="EVER CONCERT", voyage="0800-088N", etd=date(2026, 8, 3))

        progress = []
        result = create_shipment_folder(
            source_folder=source, target_parent=tmp / "2026",
            final_folder_name=target_name, do_pdf_path=do_pdf,
            new_sequence=24, progress=progress.append)

        dest = result.destination
        check("destination folder created with the PRD name", dest.is_dir()
              and dest.name == target_name)
        check("no staging folder left behind",
              not any(p.name.startswith(".") for p in (tmp / "2026").iterdir()))
        check("progress reported every step", len(progress) >= 6)

        files = sorted(p.name for p in dest.iterdir() if p.is_file())
        dirs = sorted(p.name for p in dest.iterdir() if p.is_dir())
        print(f" result files: {files}")

        check("main workbook renamed to AMJ24",
              "AMJ24-VGM,SI,Inv,PL.xls" in files)
        check("invoice workbook renamed to AMJ24",
              "Invoice tagihan AMJ24.xlsx" in files)
        check("old AMJ23 workbook is gone",
              "AMJ23-VGM,SI,Inv,PL.xls" not in files)
        check("import permit kept", any(f.lower().startswith("ip-") for f in files))
        check("DO pdf copied in", do_pdf.name in files)
        check("no stray extra root files", len(files) == 4)

        check("subfolders preserved", dirs == sorted(
            ["Draf", "PEB & NPE", "Foto", "Fumi", "Dok kirim", "PDF"]))
        check("subfolders emptied",
              all(not any((dest / d).iterdir()) for d in dirs))

        check("returned main workbook path is correct",
              result.main_workbook is not None and result.main_workbook.is_file()
              and result.main_workbook.parent == dest)
        check("permit is discoverable in the new folder",
              find_permit(dest) is not None)

        # Collision must refuse and leave the existing folder untouched.
        try:
            create_shipment_folder(
                source_folder=source, target_parent=tmp / "2026",
                final_folder_name=target_name, do_pdf_path=do_pdf, new_sequence=24)
            check("name collision refused", False)
        except FileOpError as exc:
            check("name collision refused", "sudah ada" in str(exc))
        check("collision left the existing folder intact",
              len(sorted(dest.iterdir())) == 10)

        # A bad DO path must fail before creating anything.
        before = set(p.name for p in (tmp / "2026").iterdir())
        try:
            create_shipment_folder(
                source_folder=source, target_parent=tmp / "2026",
                final_folder_name="25.rollback-test", do_pdf_path=tmp / "missing.pdf",
                new_sequence=25)
            check("missing DO refused", False)
        except FileOpError:
            check("missing DO refused", True)
        check("failed run created nothing",
              set(p.name for p in (tmp / "2026").iterdir()) == before)

# ---- deleting a shipment folder (to the Recycle Bin) ---------------------
from bot_kalung.services.fileops import delete_shipment_folder

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)

    # A real folder with contents goes to the Recycle Bin and disappears.
    live = tmp / "24.2x40-karachi"
    (live / "Draf").mkdir(parents=True)
    (live / "AMJ24-VGM,SI,Inv,PL.xls").write_text("x", encoding="utf-8")
    result = delete_shipment_folder(live)
    check("folder delete reports success", result.removed and result.existed)
    check("folder delete went to the Recycle Bin", result.recycled)
    check("folder is actually gone", not live.exists())

    # An already-missing folder is a no-op success, not an error.
    gone = delete_shipment_folder(tmp / "never-existed")
    check("missing folder is a no-op success",
          gone.removed and not gone.existed and not gone.recycled)

    # A shipment with no folder path at all is handled.
    none = delete_shipment_folder(None)
    check("empty folder path is handled", none.removed and not none.existed)

    # A file where a folder is expected is refused, not silently trashed.
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
