"""Folder-scan tracker: series discovery, done-detection, contiguous run, delta.

Builds a synthetic Drive tree mirroring the live layouts (flat, year subfolder,
nested year, three sibling brands, a no-workbook series) and drives the scanner
with an injected PDF reader so no real PDFs or Excel are needed.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.services import drive, scanner

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def make_shipment(parent: Path, folder_name: str, code_seq: str | None):
    """A numbered shipment folder; with a main workbook when code_seq is given."""
    folder = parent / folder_name
    folder.mkdir(parents=True)
    if code_seq is not None:
        (folder / f"{code_seq}-Karachi-VGM,SI,Inv,PL.xlsx").write_text("x", encoding="utf-8")
    return folder


def add_file(folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_text("x", encoding="utf-8")
    return path


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    # --- excluded top-level folders (each with a numbered folder to be sure) --
    for name in ("PENGELUARAN", "PPH", "RELASI", "GETAH ROMI",
                 "LHOONG SETIA MINING", "zzz JANGAN DISENTUH"):
        make_shipment(root / name, "1.something", "XXX01")

    # --- AMJ: year layout, a gap at 4, a prior year to ignore ----------------
    amj = root / "AMJ" / "2026"
    amj01 = make_shipment(amj, "1.5x40-karachi", "AMJ01")
    amj02 = make_shipment(amj, "2.5x40-karachi", "AMJ02")
    amj03 = make_shipment(amj, "3.5x40-karachi", "AMJ03")
    make_shipment(amj, "5.5x40-karachi", "AMJ05")          # gap at 4 -> waits
    make_shipment(root / "AMJ" / "2025", "9.old", "AMJ09")  # prior year ignored

    # --- HOPSON: year layout, HAI code ---------------------------------------
    make_shipment(root / "HOPSON" / "2026", "26.CP-2iso", "HAI26")

    # --- Three star-waleed: flat (numbered at the exporter root) -------------
    make_shipment(root / "Three star-waleed", "1.1x40-karachi", "TSI01")

    # --- Ismeth: flat, but no main workbook -> identified-but-skipped --------
    ismeth = make_shipment(root / "Ismeth-Indo Bintang Rezki", "1.Indo07-2x40", None)
    (ismeth / "Inv-IBR01.xlsx").write_text("x", encoding="utf-8")  # not a main wb

    # --- NMEHMOOD & CV.Hassan: 2026 plus a nested CV.Hassan/2026 (depth 3) ---
    nm = root / "NMEHMOOD & CV.Hassan"
    make_shipment(nm / "2026", "1.5x40-karachi", "NIT01")
    make_shipment(nm / "2026", "2.5x40-karachi", "NIT02")
    make_shipment(nm / "CV.Hassan" / "2026", "1.4x40-karachi", "HCIT01")

    # --- TASHA-HUSSAIN-MAJEED: three sibling brand folders -------------------
    thm = root / "TASHA-HUSSAIN-MAJEED"
    make_shipment(thm / "2026 Tasha", "1.2x40-karachi", "TTJ01")
    make_shipment(thm / "2026 MAJEED", "1.1x40-karachi", "MK01")
    make_shipment(thm / "2026 Hussain", "1.1x40-karachi", "HTJ01")
    make_shipment(thm / "2025-Tasha  Hussain", "9.old", "TTJ09")  # prior year

    # ==== discovery =========================================================
    series = drive.discover_series(root, 2026)
    labels = {s.label for s in series}
    check("discovers the AMJ 2026 series", "AMJ / 2026" in labels)
    check("discovers the flat Three star-waleed root", "Three star-waleed" in labels)
    check("discovers the flat Ismeth root", "Ismeth-Indo Bintang Rezki" in labels)
    check("discovers the nested CV.Hassan/2026 (depth 3)",
          "NMEHMOOD & CV.Hassan / CV.Hassan / 2026" in labels)
    check("discovers all three TASHA brand folders",
          {"TASHA-HUSSAIN-MAJEED / 2026 Tasha",
           "TASHA-HUSSAIN-MAJEED / 2026 MAJEED",
           "TASHA-HUSSAIN-MAJEED / 2026 Hussain"} <= labels)
    check("ignores prior-year folders",
          not any("2025" in l for l in labels))
    check("skips excluded + DB folders",
          not any(l.split(" /")[0] in {"PENGELUARAN", "PPH", "RELASI",
                                       "GETAH ROMI", "LHOONG SETIA MINING",
                                       "zzz JANGAN DISENTUH"} for l in labels))
    check("finds exactly the nine live-shaped series", len(series) == 9)

    # ==== identity (folder prefix is the source of truth) ===================
    check("identifies a shipment code from its main workbook",
          drive.shipment_identity(amj01) == ("AMJ", 1))
    check("a no-workbook folder has no identity",
          drive.shipment_identity(ismeth) is None)
    # A stray folder whose workbook is misnumbered (AMJ04 in a "40." folder)
    # takes its sequence from the folder, not the workbook.
    stray = make_shipment(amj, "40.1x40-Faizal", "AMJ04")
    check("sequence is the folder prefix, not the workbook number",
          drive.shipment_identity(stray) == ("AMJ", 40))

    # ==== largest contiguous run ============================================
    check("the largest block wins over a stray low sequence",
          scanner.contiguous_run({4, 14, 15, 16, 17, 18, 19, 20, 21, 22})
          == {14, 15, 16, 17, 18, 19, 20, 21, 22})
    check("a jumped-ahead number waits behind the gap",
          scanner.contiguous_run({20, 21, 22, 25}) == {20, 21, 22})
    check("a full run is kept whole",
          scanner.contiguous_run({1, 2, 3}) == {1, 2, 3})
    check("an empty set yields an empty run", scanner.contiguous_run(set()) == set())
    check("on a tie the more recent block wins",
          scanner.contiguous_run({1, 2, 5, 6}) == {5, 6})

    # ==== done-detection (real BL naming: BL/OBL token, image scans) ========
    bl_img = add_file(amj01 / "Dok kirim", "AMJ1-OBL.pdf")   # scanned image, no text
    coo = add_file(amj02 / "Dok kirim", "AMJ2-COO.pdf")      # non-BL doc only
    # Out-of-run folders (beyond the gap at 4) used only for is_done unit checks.
    linebreak = make_shipment(amj, "8.linebreak", "AMJ08")
    bl_txt = add_file(linebreak / "Dok kirim", "AMJ8-BL.pdf")   # marker wraps a line
    notbl = make_shipment(amj, "9.notbl", "AMJ09")
    bl_fake = add_file(notbl / "Dok kirim", "AMJ9-BL.pdf")      # BL name, no marker

    texts = {str(bl_img): "", str(coo): "CERTIFICATE OF ORIGIN",
             str(bl_txt): "SHIPPER\nBILL OF\nLADING\nno 5",
             str(bl_fake): "SOME UNRELATED DOCUMENT"}
    reader = lambda p: texts.get(str(p), "")

    check("a scanned-image BL (no text) is done by its BL name",
          scanner.is_done(amj01, page1_text=reader))
    check("a send folder with no BL document is not done",
          not scanner.is_done(amj02, page1_text=reader))
    check("a BL marker split across lines is detected",
          scanner.is_done(linebreak, page1_text=reader))
    check("a BL-named PDF whose text lacks the marker is not done",
          not scanner.is_done(notbl, page1_text=reader))

    # ==== the scan plan =====================================================
    result = scanner.scan(root, 2026, set(), page1_text=reader)
    imported = {c.label for c in result.to_import}
    done = {c.label for c in result.done}

    # Labels follow the app's convention of an unpadded integer sequence
    # ({exporter_code}{sequence_number}), so folder "1.5x40" -> "AMJ1".
    check("AMJ1 (has a BL scan) is reported done", "AMJ1" in done)
    check("AMJ1 is not imported", "AMJ1" not in imported)
    check("AMJ2 and AMJ3 (in-run, not done) are imported",
          {"AMJ2", "AMJ3"} <= imported)
    check("AMJ5 (behind the gap) is neither imported nor done",
          "AMJ5" not in imported and "AMJ5" not in done)
    check("the stray AMJ40 is excluded by the largest-run gate",
          "AMJ40" not in imported and "AMJ40" not in done)
    check("the nested HCIT1 is imported", "HCIT1" in imported)
    check("all three TASHA brands are imported",
          {"TTJ1", "MK1", "HTJ1"} <= imported)
    check("HAI26 and TSI1 are imported", {"HAI26", "TSI1"} <= imported)
    check("the no-workbook Ismeth folder is not imported",
          not any(c.label.startswith("IBR") or c.label.startswith("Indo")
                  for c in result.to_import))
    check("the discovery report flags Ismeth's skipped folder",
          any("Ismeth" in note and "dilewati" in note for note in result.report))

    # ==== delta (registry) ==================================================
    delta = scanner.scan(root, 2026, {("AMJ", 2), ("NIT", 1)}, page1_text=reader)
    delta_imported = {c.label for c in delta.to_import}
    check("an already-registered shipment is not re-imported",
          "AMJ2" not in delta_imported and "NIT1" not in delta_imported)
    check("still-new shipments are imported on a delta scan",
          "AMJ3" in delta_imported and "NIT2" in delta_imported)


print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Scanner OK - all checks passed.")
