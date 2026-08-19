"""Drive layer tests using the real folder and filename conventions of all five
exporters, including the anomalies found on the live Drive.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

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

    # --- filename derivation (canonical output name) ------------------------
    # {code}{seq} - {destination} - VGM,SI,Inv,PL{ext}, extension preserved.
    check("AMJ workbook is canonical and keeps .xls",
          drive.derive_main_workbook_name(src_amj, 24, "Karachi")
          == "AMJ24 - Karachi - VGM,SI,Inv,PL.xls")
    check("AMJ invoice keeps 'Invoice tagihan' form",
          drive.derive_invoice_name(src_amj, 24) == "Invoice tagihan AMJ24.xlsx")

    check("TTJ workbook is canonical with its destination and padding",
          drive.derive_main_workbook_name(src_ttj, 5, "Karachi")
          == "TTJ05 - Karachi - VGM,SI,Inv,PL.xlsx")
    check("TTJ invoice keeps Inv- form and padding",
          drive.derive_invoice_name(src_ttj, 5) == "Inv-TTJ05.xlsx")

    check("NIT workbook takes the destination it is given",
          drive.derive_main_workbook_name(src_nit, 16, "CHENNAI")
          == "NIT16 - CHENNAI - VGM,SI,Inv,PL.xlsx")
    check("NIT invoice renumbers", drive.derive_invoice_name(src_nit, 16) == "Inv-NIT16.xlsx")

    check("THREESTAR workbook keeps the TSI code and padding",
          drive.derive_main_workbook_name(src_tsi, 2, "Karachi")
          == "TSI02 - Karachi - VGM,SI,Inv,PL.xlsx")
    check("THREESTAR invoice keeps padding",
          drive.derive_invoice_name(src_tsi, 2) == "Inv-TSI02.xlsx")

    check("padding widens correctly at 9 -> 10",
          drive.derive_main_workbook_name(src_ttj, 10, "Karachi")
          == "TTJ10 - Karachi - VGM,SI,Inv,PL.xlsx")
    check("a blank destination drops the middle segment",
          drive.derive_main_workbook_name(src_amj, 24)
          == "AMJ24 - VGM,SI,Inv,PL.xls")

    # --- token-based detection (any real Drive variant) --------------------
    detected = {
        "AMJ09-VGM,SI,Inv,P.List.xls": 9,          # P.List spelling
        "LSM01-1x20-VGM,SI,Inv,P.List.xlsx": 1,    # size middle + P.List
        "AMJ18VGM,SI,Inv,PL.xls": 18,              # no hyphen before VGM
        "AMJ29-Vgm,SI,Inv,PL......xls": 29,        # trailing dots
        "GGN-00004-VGM,SI,Inv,PL.xls": 4,          # hyphen code-seq
        "TTJ04-Karachi-VGM,SI,INV,PL.xlsx": 4,     # already worked
        "AMJ24 - Karachi - VGM,SI,Inv,PL.xlsx": 24,  # the new canonical name
        "AMJ23 - Karachi - VGM SI INV PL.xlsx": 23,  # spaces, no commas
    }
    for name, seq in detected.items():
        check(f"detects {name}", drive.is_main_workbook(name)
              and drive.main_workbook_sequence(name) == seq)

    # A bare workbook is still the workbook (kept + renamed); its sequence just
    # falls back to the folder prefix.
    check("a bare workbook is detected", drive.is_main_workbook("VGM,SI,Inv,PL.xlsx"))
    check("a bare workbook has no sequence in its name",
          drive.main_workbook_sequence("VGM,SI,Inv,PL.xlsx") is None)

    not_workbooks = [
        "VGM I-822-17 PONTIANAK.xls",             # VGM only, not a workbook
        "LJA 01 VGM SS490 20 Apr .xls",           # VGM only
        "NIT02-Karachi-VGM,SI,INV,PL (1).xlsx",   # a duplicate copy
        "Invoice tagihan AMJ23.xlsx",             # the invoice
        "Inv-TTJ04.xlsx",                         # the invoice
        "DO 2503357.pdf",                         # a PDF, not Excel
    ]
    for name in not_workbooks:
        check(f"not a workbook: {name}", not drive.is_main_workbook(name))

    check("the invoice is recognised as such", drive.is_invoice("Inv-TTJ04.xlsx"))
    check("the workbook is not mistaken for an invoice",
          not drive.is_invoice("TTJ04-Karachi-VGM,SI,INV,PL.xlsx"))

    # derive renumbers regardless of the source shape, always canonical output
    variants_dir = root / "_variants"
    variants_dir.mkdir()
    plist = make(variants_dir, "09.1x40-plistdest", ["AMJ09-VGM,SI,Inv,P.List.xls"])
    check("a P.List source produces the canonical name and keeps .xls",
          drive.derive_main_workbook_name(plist, 10, "Karachi")
          == "AMJ10 - Karachi - VGM,SI,Inv,PL.xls")
    ggn = make(variants_dir, "04.1x40-ggndest", ["GGN-00004-VGM,SI,Inv,PL.xls"])
    check("a hyphenated code renumbers into the canonical name",
          drive.derive_main_workbook_name(ggn, 5, "Xiamen")
          == "GGN00005 - Xiamen - VGM,SI,Inv,PL.xls")

    # --- template folder chosen by destination ------------------------------
    tsf = root / "_tsf"
    tsf_year = tsf / "2026"
    tsf_year.mkdir(parents=True)
    make(tsf_year, "01.1x40-karachi-a", ["X01-VGM,SI,INV,PL.xlsx"])
    make(tsf_year, "02.1x40-chennai-b", ["X02-VGM,SI,INV,PL.xlsx"])
    make(tsf_year, "03.1x40-karachi-c", ["X03-VGM,SI,INV,PL.xlsx"])
    make(tsf_year, "04.1x40-dubai-d",   ["X04-VGM,SI,INV,PL.xlsx"])
    check("destination picks the most recent matching folder, not the last",
          drive.template_source_folder(tsf, "AMJ", 2026, "KARACHI", s).name
          == "03.1x40-karachi-c")
    check("a different destination picks its own folder",
          drive.template_source_folder(tsf, "AMJ", 2026, "chennai", s).name
          == "02.1x40-chennai-b")
    check("no destination match falls back to the last folder",
          drive.template_source_folder(tsf, "AMJ", 2026, "tokyo", s).name
          == "04.1x40-dubai-d")
    check("a blank destination falls back to the last folder",
          drive.template_source_folder(tsf, "AMJ", 2026, "", s).name
          == "04.1x40-dubai-d")
    check("an exporter with no folders yields no template",
          drive.template_source_folder(root / "_nope", "AMJ", 2026, "karachi", s)
          is None)

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
