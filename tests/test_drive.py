"""Drive layer tests using the real folder and filename conventions of all five
exporters, including the anomalies found on the live Drive.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.constants import EXPORTERS
from bot_kalung.core.db import Database, db_path_for
from bot_kalung.core.settings import Settings
from bot_kalung.services import drive

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def make(base: Path, folder: str, files: list[str], dirs: list[str] = ()):
    path = base / folder
    path.mkdir(parents=True)
    for name in files:
        (path / name).write_text("x", encoding="utf-8")
    for name in dirs:
        (path / name).mkdir()
    return path


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "My Drive"
    root.mkdir()

    # --- AMJ: {exporter}/{year}/, .xls, "Invoice tagihan" ------------------
    amj = root / "AMJ" / "2026"
    amj.mkdir(parents=True)
    (root / "AMJ" / "2026" / "1-30").mkdir()
    make(amj, "22.2x40-karachi-Salim-KLN-EVE-084600048553-Concert 0800-088N-03 aug",
         ["AMJ22-VGM,SI,Inv,PL.xls", "Invoice tagihan AMJ22.xlsx"],
         ["Draf", "PEB & NPE", "Foto", "Fumi", "Dok kirim", "PDF"])
    src_amj = make(amj, "23.2x40-karachi-Salim-KLN-EVE-084600048570-Concert 0800-088N-03 aug",
                   ["AMJ23-VGM,SI,Inv,PL.xls", "Invoice tagihan AMJ23.xlsx",
                    "IP-KHI-E2E018_2026.pdf"],
                   ["Draf", "PEB & NPE", "Foto", "Fumi", "Dok kirim", "PDF"])
    # The anomaly: folder prefix says 40, documents say AMJ04.
    make(amj, "40.1x40-Faizal",
         ["AMJ04-VGM,SI,Inv,PL.xls", "Invoice tagihan AMJ04.xlsx"], ["PDF", "Foto"])

    # --- TTJ: {exporter}/{year} Tasha/, zero-padded, destination in name ----
    ttj = root / "TASHA-HUSSAIN-MAJEED" / "2026 Tasha"
    ttj.mkdir(parents=True)
    (root / "TASHA-HUSSAIN-MAJEED" / "2026 MAJEED").mkdir()
    (root / "TASHA-HUSSAIN-MAJEED" / "2026 Hussain" / "1.1x40-PT.Hussain").mkdir(parents=True)
    make(ttj, "01.1x40-Karachi-MESF09578500-S.Bajo105N-21 jan",
         ["TTJ01-Karachi-VGM,SI,INV,PL.xlsx", "Inv-TTJ01.xlsx"])
    src_ttj = make(ttj, "04.3x40-Karachi-Sunli-OOCL2331585571-Integra179E",
                   ["TTJ04-Karachi-VGM,SI,INV,PL.xlsx", "Inv-TTJ04.xlsx"],
                   ["Ke Mandiri", "PEB & NPE", "Draf", "Fumi", "PDF", "Foto", "Dok Kirim"])

    # --- NIT: {exporter}/{year}/, label between seq and qty -----------------
    nit = root / "NMEHMOOD & CV.Hassan" / "2026"
    nit.mkdir(parents=True)
    make(nit, "8.PKBM-3x40-Karachi-Gan-OOCL-2326582820-Integra174 ke 175E",
         ["NIT08-Karachi-VGM,SI,INV,PL.xlsx", "Inv-NIT08.xlsx"])
    src_nit = make(nit, "15.10x40-Chennai-Maruti-GAN-T22854-Reya123N-25 july",
                   ["NIT15-CHENNAI-VGM,SI,INV,PL.xlsx", "Inv-NIT15.xlsx"])

    # --- THREESTAR: no year folder, TSI code --------------------------------
    tsi = root / "Three star-waleed"
    tsi.mkdir()
    (tsi / "Surat").mkdir()
    src_tsi = make(tsi, "1.1x40-Karachi-GAN-OOCL2331904123-Integra182E-2 aug",
                   ["TSI01-Karachi-VGM,SI,INV,PL.xlsx", "Inv-TSI01.xlsx"],
                   ["Dok Kirim", "Draft", "PDF", "Foto"])

    db = Database(db_path_for(root))
    db.initialize()
    s = Settings(db)

    # --- resolution --------------------------------------------------------
    check("4 exporters: TASHA folded into TTJ, INDO dropped",
          EXPORTERS == ["AMJ", "TTJ", "NIT", "THREESTAR"])
    ok, found = drive.validate_drive_root(root, s)
    check("all 4 exporter folders resolve by default", ok and len(found) == 4)
    check("NIT resolves to NMEHMOOD & CV.Hassan",
          drive.resolve_exporter_folder(root, "NIT", s).name == "NMEHMOOD & CV.Hassan")

    # --- layout variants ---------------------------------------------------
    check("AMJ shipment dir is {exporter}/2026",
          drive.shipment_dir(root / "AMJ", "AMJ", 2026, s).name == "2026")
    check("TTJ shipment dir is '2026 Tasha'",
          drive.shipment_dir(root / "TASHA-HUSSAIN-MAJEED", "TTJ", 2026, s).name
          == "2026 Tasha")
    check("THREESTAR has no year subfolder",
          drive.shipment_dir(tsi, "THREESTAR", 2026, s) == tsi)

    # --- sequence detection ------------------------------------------------
    amj_root = root / "AMJ"
    seqs = [n for n, _ in drive.scan_shipment_folders(amj_root, "AMJ", 2026, s)]
    check("AMJ reads 40.1x40-Faizal as sequence 4, not 40", 40 not in seqs and 4 in seqs)
    check("AMJ next sequence is 24", drive.next_sequence_number(amj_root, "AMJ", 2026, s) == 24)
    check("AMJ source folder is 23.*",
          drive.latest_shipment_folder(amj_root, "AMJ", 2026, s)[1].name.startswith("23."))
    check("'1-30' archive folder ignored", len(seqs) == 3)

    ttj_root = root / "TASHA-HUSSAIN-MAJEED"
    check("TTJ next sequence is 5 (zero-padded source)",
          drive.next_sequence_number(ttj_root, "TTJ", 2026, s) == 5)
    check("TTJ ignores the sibling MAJEED and Hussain folders",
          len(drive.scan_shipment_folders(ttj_root, "TTJ", 2026, s)) == 2)

    nit_root = root / "NMEHMOOD & CV.Hassan"
    check("NIT next sequence is 16", drive.next_sequence_number(nit_root, "NIT", 2026, s) == 16)
    check("NIT reads the PKBM-labelled folder as 8",
          8 in [n for n, _ in drive.scan_shipment_folders(nit_root, "NIT", 2026, s)])

    check("THREESTAR next sequence is 2",
          drive.next_sequence_number(tsi, "THREESTAR", 2026, s) == 2)

    # --- filename derivation ------------------------------------------------
    check("AMJ workbook keeps .xls and Inv,PL casing",
          drive.derive_main_workbook_name(src_amj, 24) == "AMJ24-VGM,SI,Inv,PL.xls")
    check("AMJ invoice keeps 'Invoice tagihan' form",
          drive.derive_invoice_name(src_amj, 24) == "Invoice tagihan AMJ24.xlsx")

    check("TTJ workbook keeps destination, padding and INV,PL casing",
          drive.derive_main_workbook_name(src_ttj, 5) == "TTJ05-Karachi-VGM,SI,INV,PL.xlsx")
    check("TTJ invoice keeps Inv- form and padding",
          drive.derive_invoice_name(src_ttj, 5) == "Inv-TTJ05.xlsx")

    check("NIT workbook keeps uppercase destination",
          drive.derive_main_workbook_name(src_nit, 16) == "NIT16-CHENNAI-VGM,SI,INV,PL.xlsx")
    check("NIT invoice renumbers", drive.derive_invoice_name(src_nit, 16) == "Inv-NIT16.xlsx")

    check("THREESTAR workbook keeps TSI code and padding",
          drive.derive_main_workbook_name(src_tsi, 2) == "TSI02-Karachi-VGM,SI,INV,PL.xlsx")
    check("THREESTAR invoice keeps padding",
          drive.derive_invoice_name(src_tsi, 2) == "Inv-TSI02.xlsx")

    check("padding widens correctly at 9 -> 10",
          drive.derive_main_workbook_name(src_ttj, 10) == "TTJ10-Karachi-VGM,SI,INV,PL.xlsx")

    # --- per-exporter subfolders --------------------------------------------
    check("AMJ subfolders read from disk",
          drive.source_subfolders(src_amj)
          == ["Dok kirim", "Draf", "Foto", "Fumi", "PDF", "PEB & NPE"])
    check("TTJ subfolders include 'Ke Mandiri'",
          "Ke Mandiri" in drive.source_subfolders(src_ttj))
    check("THREESTAR subfolders omit Fumi and PEB & NPE",
          drive.source_subfolders(src_tsi) == ["Dok Kirim", "Draft", "Foto", "PDF"])

    check("file code for THREESTAR is TSI", drive.file_code("THREESTAR", s) == "TSI")
    check("file code for AMJ is AMJ", drive.file_code("AMJ", s) == "AMJ")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Drive layer OK — all checks passed.")
