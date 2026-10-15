"""Action-item status from shipment-folder files (doc automation Phase 1A)."""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.db import Database, db_path_for, new_id
from bot_kalung.services import doc_status as ds
from bot_kalung.services.action_items import ActionItems
from bot_kalung.services.doc_status import DocFile

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def F(folder, name, created="2026-10-01T00:00:00"):
    return DocFile(folder, name, f"ref:{folder}/{name}", created)


def status(code, files, exporter="AMJ"):
    inf = ds.infer(code, files, exporter)
    return inf.status if inf else None


# ---- folder kinds (live spellings) ----------------------------------------
check("folder spellings are classified",
      [ds.folder_kind(n) for n in ("Draf", "Draft", "Dok kirim", "Dok Kirim", "PEB & NPE",
                                   "PEB&NPE", "PDF", "Fumi", "doc ami", "Dok PMA", "Ke Mandiri",
                                   "BCA", "Foto")]
      == ["draft", "draft", "kirim", "kirim", "peb", "peb", "pdf", "fumi", "dok_ami",
          "dok_pma", "bank", "bank", "other"])

# ---- SI/VGM/INV/PL --------------------------------------------------------
check("SI: Dikerjakan with no INV/PL in Dok kirim", status("si_vgm", []) == "in_progress")
check("SI: INV alone is not enough",
      status("si_vgm", [F("kirim", "AMJ27-Karachi-Inv.pdf")]) == "in_progress")
check("SI: INV + PL in Dok kirim -> Final",
      status("si_vgm", [F("kirim", "AMJ27-Karachi-Inv.pdf"), F("kirim", "AMJ27-Karachi-PL.pdf")]) == "final")
check("SI: a DHL courier invoice is not the INV",
      status("si_vgm", [F("kirim", "DHL Inv-HAI30.pdf"), F("kirim", "HAI32-P.List.pdf")]) == "in_progress")
check("SI AMI: Final once 'doc ami' has a file",
      status("si_vgm", [F("dok_ami", "SI 31 AB EXPORT.pdf")], "AMI") == "final"
      and status("si_vgm", [F("kirim", "x-INV.pdf"), F("kirim", "x-PL.pdf")], "AMI") == "in_progress")
check("SI CKJ: Final once PDF/ has a file",
      status("si_vgm", [F("pdf", "CKJ25-SI.pdf")], "CKJ") == "final")

# ---- PEB/NPE --------------------------------------------------------------
check("PEB in Draf -> Draft diterima", status("peb_npe", [F("draft", "PEB 27092026.pdf")]) == "draft_received")
check("PEB ttd in PEB & NPE -> Final",
      status("peb_npe", [F("draft", "PEB 27092026.pdf"), F("peb", "AMJ27-PEB ttd.pdf"),
                         F("peb", "NPE 27092026.pdf")]) == "final")
check("an unsigned PEB in PEB & NPE alone is not Final",
      status("peb_npe", [F("peb", "PEB 27092026.pdf")]) is None)

# ---- Fumi / Phyto / COO / BL ----------------------------------------------
check("Fumi: file in Fumi/ -> Menunggu Hardcopy",
      status("fumi", [F("fumi", "RECORDSHEET CV. AGUNG.pdf")]) == "waiting_hardcopy")
check("Fumi: fumi in Dok kirim -> Hardcopy diterima",
      status("fumi", [F("fumi", "RECORDSHEET.pdf"), F("kirim", "AMJ27-Fumi.pdf")]) == "hardcopy_received")
check("Phyto: Draf -> Draft diterima, Dok kirim -> Final",
      status("phyto", [F("draft", "AMJ DRAF PHYTO 27092026.pdf")]) == "draft_received"
      and status("phyto", [F("kirim", "PHYTO 27092026.pdf")]) == "final")
check("COO: Draf -> Draft diterima, Dok kirim -> Hardcopy diterima",
      status("coo", [F("draft", "AMJ COO 27092026.pdf")]) == "draft_received"
      and status("coo", [F("kirim", "AMJ27-COO.pdf")]) == "hardcopy_received")
from dataclasses import replace as _replace
fumi_docx = _replace(F("draft", "PT. ABDUAL MANAN INDONESIA 0801CN.docx"), kind="fumi")
check("a file the email reader saved under the sender's name is known by its recorded type",
      status("fumi", [fumi_docx]) == "draft_received"
      and status("fumi", [F("draft", "PT. ABDUAL MANAN INDONESIA 0801CN.docx")]) is None
      and status("insurance", [_replace(F("kirim", "PT. HOPSON CERT. 7.pdf"), kind="insurance")], "HAI")
      == "hardcopy_received")
check("Insurance: draft in Draf -> Draft diterima; in Dok kirim -> Hardcopy diterima "
      "(the insurer's invoice doesn't count)",
      status("insurance", [F("draft", "HAI35-Ins draft.pdf")], "HAI") == "draft_received"
      and status("insurance", [F("kirim", "HAI35-Ins.pdf")], "HAI") == "hardcopy_received"
      and status("insurance", [F("kirim", "ACA Inv-HAI35.pdf")], "HAI") is None)
check("BL: OBL / SWB / Copy BL in Dok kirim -> Final",
      all(status("bl", [F("kirim", n)]) == "final"
          for n in ("AMJ27-OBL.pdf", "SWB 2335150950.pdf", "IMG_20260915_0004-CopyBL.pdf",
                    "SCAN BL TI2BLWKAT02931.pdf", "TTJ07-BL.pdf")))
check("BL: 'BLW' inside a name is not a BL",
      status("bl", [F("kirim", "CI 31 AB EXPORT IMPORT BLW22931.pdf")]) is None)
check("BL: carrier draft in Draf -> Draft diterima",
      status("bl", [F("draft", "2334357090-20260914065426 (1).PDF")]) == "draft_received")

# ---- Dok Bank ---------------------------------------------------------------
check("Dok Bank: Final only once the bank folder holds a PDF",
      status("dok_bank", [F("bank", "Dok ke Mandiri.xlsx")]) is None
      and status("dok_bank", [F("bank", "TTJ07-Mandiri tt.pdf")]) == "final")

# ---- REV / NOTUL re-adjusts (can move backward) ---------------------------
peb_final = [F("draft", "PEB 1.pdf", "2026-10-01T00:00:00"),
             F("peb", "X-PEB ttd.pdf", "2026-10-02T00:00:00")]
rev_after = peb_final + [F("draft", "REV PEB 1.pdf", "2026-10-03T00:00:00")]
rev_before = peb_final + [F("draft", "REV PEB 1.pdf", "2026-09-30T00:00:00")]
inf = ds.infer("peb_npe", rev_after, "AMJ")
check("a newer REV draft re-adjusts PEB back to Draft diterima",
      inf.status == "draft_received" and inf.is_rev and inf.file.name == "REV PEB 1.pdf")
check("an older REV draft does not override the newer Final",
      status("peb_npe", rev_before) == "final")
check("a newer signed NOTUL PEB is Final again",
      status("peb_npe", rev_after + [F("peb", "Notul X-PEB ttd.pdf", "2026-10-04T00:00:00")]) == "final")

# ---- files copied from another shipment are ignored (HAI39 was a copy of HAI32)
copied = [F("draft", "PEB 32092026.pdf"), F("draft", "HAI COO 32092026.pdf"),
          F("kirim", "HAI32-Inv cnee.pdf"), F("kirim", "HAI32-P.List cnee.pdf")]
check("another shipment's files don't count (HAI32 files in HAI39)",
      ds.infer("peb_npe", copied, "HAI", {39}) is None
      and ds.infer("coo", copied, "HAI", {39}) is None
      and ds.infer("si_vgm", copied, "HAI", {39}).status == "in_progress")
check("the shipment's own numbers still count (incl. the workbook's, MA2 = MA027)",
      ds.infer("peb_npe", [F("draft", "PEB 39102026.pdf")], "HAI", {39}).status == "draft_received"
      and ds.infer("peb_npe", [F("draft", "PEB 027082026.pdf")], "MA", {2, 27}).status == "draft_received")
check("an alias code (HSN for HCIT) is not treated as foreign",
      ds.infer("fumi", [F("kirim", "HSN05-Fumi.pdf")], "HCIT", {5}).status == "hardcopy_received")
check("a carrier-dated scan name isn't mistaken for a sequence",
      ds.infer("bl", [F("kirim", "IMG_20260915_0004-CopyBL.pdf")], "AMJ", {27}).status == "final")
check("own_sequences reads the main workbook's number",
      ds.own_sequences({"sequence_number": 2}, [F("root", "MA027-VGM,SI,Inv,P.List.xls")]) == {2, 27})
check("an ACA (insurance) invoice is not the commercial INV",
      ds.infer("si_vgm", [F("kirim", "ACA Inv-HAI35.pdf"), F("kirim", "Hai35-P.List cnee.pdf")],
               "HAI", {35}).status == "in_progress")

# ---- decide: forward always, backward only via REV/NOTUL, manual sticks ---
fwd = ds.Inferred("final", F("kirim", "a.pdf"))
check("forward move applies", ds.decide("draft_received", None, None, fwd) == "final")
check("non-REV backward move is ignored (e.g. a manual Hardcopy diterima)",
      ds.decide("hardcopy_received", None, None, ds.Inferred("final", F("kirim", "PHYTO.pdf"))) is None)
check("REV backward move applies",
      ds.decide("final", "final", "ref:peb/X-PEB ttd.pdf", inf) == "draft_received")
check("an older REV doesn't undo a status set after it (JMI27)",
      ds.decide("final", None, None, inf, last_change="2026-10-05T00:00:00") is None
      and ds.decide("final", None, None, inf, last_change="2026-10-02T00:00:00") == "draft_received")
revised = ds.Inferred("draft_received", F("draft", "PEB draft 2.pdf", "2026-10-03T00:00:00"))
check("revision loop: a new draft after Draf direvisi -> Draft diterima",
      ds.decide("draft_revision", "draft_revision", "ref:draft/PEB draft.pdf", revised,
                last_change="2026-10-02T00:00:00") == "draft_received")
check("...but not a draft older than the revision request",
      ds.decide("draft_revision", "draft_revision", "ref:draft/PEB draft.pdf", revised,
                last_change="2026-10-04T00:00:00") is None)
remarked = ds.Inferred("draft_revision", ds.DocFile("draft", "PEB draft.pdf", "ref:draft/PEB draft.pdf",
                                                     "2026-10-01T00:00:00", "2026-10-05T00:00:00"))
check("a red correction drawn after the draft was OK'd -> Draf direvisi (newest review wins)",
      ds.decide("draft_ok", "draft_ok", "ref:draft/PEB draft.pdf#ok", remarked,
                last_change="2026-10-04T00:00:00") == "draft_revision")
check("...but not marks older than the OK",
      ds.decide("draft_ok", "draft_ok", "ref:draft/PEB draft.pdf#ok", remarked,
                last_change="2026-10-06T00:00:00") is None)
check("same evidence as last time -> leave a manual edit alone",
      ds.decide("pending", "final", "ref:kirim/a.pdf", fwd) is None)

# ---- end to end on a local folder tree ------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    db = Database(db_path_for(Path(tmp)))
    db.initialize()
    items = ActionItems(db)
    sid = new_id()
    db.execute("INSERT INTO shipments (id, exporter_code, sequence_number, status, created_at) "
               "VALUES (?, 'TTJ', 7, 'active', '2026-08-18')", (sid,))
    items.seed(sid, "TTJ", "Pakistan")
    folder = Path(tmp) / "7.TTJ"
    for sub in ("Draf", "Dok Kirim", "PEB & NPE", "Fumi", "Ke Mandiri"):
        (folder / sub).mkdir(parents=True)
    for rel in ("Draf/TTJ COO 07082026.pdf", "Dok Kirim/TTJ07-COO.pdf",
                "Dok Kirim/TTJ07-Karachi-INV.pdf", "Dok Kirim/TTJ07-Karachi-PL.pdf",
                "PEB & NPE/TTJ07-PEB ttd.pdf", "Fumi/RECORDSHEET.pdf",
                "Ke Mandiri/Dok ke Mandiri.xlsx"):
        (folder / rel).write_text("x", encoding="utf-8")
    row = db.query_one("SELECT * FROM shipments WHERE id=?", (sid,))
    report = ds.sync_shipment(items, row, folder)
    st = {r["code"]: r for r in db.query("SELECT * FROM action_items WHERE shipment_id=?", (sid,))}
    check("statuses follow the folder",
          (st["si_vgm"]["status"], st["coo"]["status"], st["peb_npe"]["status"],
           st["fumi"]["status"]) == ("final", "hardcopy_received", "final", "waiting_hardcopy"))
    check("the evidence file is recorded for the UI",
          st["coo"]["auto_evidence"] == "TTJ07-COO.pdf")
    check("a bank folder adds Dok Bank, still pending without a PDF",
          "dok_bank" in st and st["dok_bank"]["status"] == "pending")
    check("each change is reported with its file",
          any("COO" in r and "TTJ07-COO.pdf" in r for r in report))
    check("a second pass with nothing new changes nothing",
          ds.sync_shipment(items, row, folder) == [])

    items.set_status(st["coo"]["id"], "draft_ok")            # someone edits by hand
    ds.sync_shipment(items, row, folder)
    check("a manual edit stays while the files are unchanged",
          db.query_one("SELECT status FROM action_items WHERE id=?", (st["coo"]["id"],))["status"]
          == "draft_ok")

    later = time.time() + 120
    rev = folder / "Draf" / "REV-TTJ COO 07082026.pdf"
    rev.write_text("x", encoding="utf-8")
    os.utime(rev, (later, later))
    ds.sync_shipment(items, row, folder)
    check("a newer REV COO draft moves COO back to Draft diterima",
          db.query_one("SELECT status, auto_evidence FROM action_items WHERE id=?",
                       (st["coo"]["id"],))["auto_evidence"] == "REV-TTJ COO 07082026.pdf"
          and db.query_one("SELECT status FROM action_items WHERE id=?",
                           (st["coo"]["id"],))["status"] == "draft_received")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Doc status OK - all checks passed.")
