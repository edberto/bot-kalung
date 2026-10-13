"""Email reader + approval queue (doc automation Phase 2) with a fake Gmail and
fake classifier — no network."""

import base64
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.db import Database, db_path_for, new_id
from bot_kalung.services import mail_inbox as mi
from bot_kalung.services.action_items import ActionItems

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


class FakeGmail:
    """Just enough of googleapiclient's Gmail surface: attachments().get()."""

    def __init__(self, files):
        self.files = files                        # attachment_id -> bytes

    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return self

    def get(self, userId, messageId, id):         # noqa: A002, N803
        self._data = self.files[id]
        return self

    def execute(self, **kw):
        return {"data": base64.urlsafe_b64encode(self._data).decode()}


def message(mid, sender, to, subject, files, when="1760000000000", thread="t1"):
    parts = [{"partId": str(i), "filename": name, "mimeType": "application/pdf",
              "body": {"attachmentId": f"{mid}-{i}", "size": 50_000}}
             for i, name in enumerate(files)]
    return {"id": mid, "threadId": thread, "internalDate": when,
            "payload": {"headers": [{"name": "From", "value": sender}, {"name": "To", "value": to},
                                    {"name": "Subject", "value": subject}], "parts": parts}}


with tempfile.TemporaryDirectory() as tmp:
    db = Database(db_path_for(Path(tmp)))
    db.initialize()
    amj, ami = new_id(), new_id()
    db.execute("INSERT INTO shipments (id, exporter_code, sequence_number, booking_number, status, "
               "created_at) VALUES (?, 'AMJ', 30, '084600063374', 'active', '2026-10-01')", (amj,))
    db.execute("INSERT INTO shipments (id, exporter_code, sequence_number, doc_seq, status, "
               "created_at) VALUES (?, 'AMI', 7, 35, 'active', '2026-10-01')", (ami,))
    items = ActionItems(db)
    items.seed(amj, "AMJ", "Pakistan")
    items.seed(ami, "AMI", "India")

    check("subject 'Re: AMJ30' -> AMJ30", mi.match_shipment(db, "Re: AMJ30")[0]["id"] == amj)
    check("AMI uses the buyer's number: 'AMI035-4X40' -> AMI7",
          mi.match_shipment(db, "Re: AMI035-4X40")[0]["id"] == ami)
    check("a BL thread by DO number -> AMJ30 (via do)",
          mi.match_shipment(db, "BL draft 084600063374")[1] == "do")
    check("an unrelated subject matches nothing", mi.match_shipment(db, "Lunch?")[0] is None)

    status = lambda sid, code: db.query_one(                                       # noqa: E731
        "SELECT status FROM action_items WHERE shipment_id=? AND code=?", (sid, code))["status"]
    pdfs = {}
    gmail = FakeGmail(pdfs)
    team = message("m1", "Ka Lung <ongkalung@gmail.com>", "Doc Exim <docexim.sbm@gmail.com>", "AMJ30",
                   ["AMJ30-Karachi-SI.pdf", "AMJ30-Karachi-VGM.pdf"])
    mi.process(db, gmail, team)
    check("team sends SI + VGM to docexim -> PEB, COO, Phyto become Dikerjakan",
          [status(amj, c) for c in ("peb_npe", "coo", "phyto")] == ["in_progress"] * 3
          and status(amj, "fumi") == "pending")
    check("team mail is never queued", not db.query("SELECT 1 FROM mail_queue"))

    pdfs.update({"m2-0": b"%PDF peb", "m2-1": b"%PDF coo"})
    reply = message("m2", "Doc Exim <docexim.sbm@gmail.com>", "ongkalung@gmail.com", "Re: AMJ30",
                    ["PEB 30.pdf", "AMJ COO 30.pdf"], when="1760000100000")
    guesses = {b"%PDF peb": {"doc_type": "peb", "stage": "draft", "correction": False},
               b"%PDF coo": {"doc_type": "coo", "stage": "draft", "correction": False}}
    mi.process(db, gmail, reply, ask=lambda data, mime: guesses[data])
    q = db.query("SELECT * FROM mail_queue ORDER BY part_id")
    check("each attachment from docexim is queued with its type, folder and name",
          [(r["doc_type"], r["dest"], r["proposed_name"]) for r in q]
          == [("peb", "draft", "AMJ30-PEB-PEB 30.pdf"), ("coo", "draft", "AMJ30-COO-AMJ COO 30.pdf")])
    mi.process(db, gmail, reply, ask=lambda data, mime: guesses[data])
    check("the same mail again adds nothing", len(db.query("SELECT 1 FROM mail_queue")) == 2)

    # after the PEB was OK'd: a new version is a correction
    db.execute("UPDATE action_items SET status='draft_ok', updated_at='2025-10-09T08:56:00' "
               "WHERE shipment_id=? AND code='peb_npe'", (amj,))
    check("a draft after OK, nobody from the team asked -> REV",
          mi.correction_prefix(db, amj, "peb_npe", "draft", "t1", "2025-10-09T09:00:00", False) == "REV")
    db.execute("INSERT INTO mail_seen (message_id, thread_id, from_team, sent_at) "
               "VALUES ('m9', 't1', 1, '2025-10-09T08:58:00')")
    check("a draft after OK that the team asked for (team mail since the OK) -> NOTUL",
          mi.correction_prefix(db, amj, "peb_npe", "draft", "t1", "2025-10-09T09:00:00", False) == "NOTUL")
    check("the first final after OK is the normal cycle -> no prefix",
          mi.correction_prefix(db, amj, "peb_npe", "final", "t1", "2025-10-09T09:00:00", False) == "")
    check("before OK (revision loop) -> no prefix",
          mi.correction_prefix(db, amj, "coo", "draft", "t1", "2025-10-09T09:00:00", False) == "")

    check("names: a BL draft is 'BL draft'; no shipment yet is marked",
          mi.file_name({"exporter_code": "AMJ", "sequence_number": 30}, "bl", "draft", "", "x.pdf")
          == "AMJ30-BL draft-x.pdf"
          and mi.file_name(None, "fumi", "final", "REV", "f.pdf") == "REV-TANPA-PENGIRIMAN-Fumi-f.pdf")
    check("finals go to Dok kirim, Fumi finals to Fumi, drafts to Draf",
          (mi.default_dest("peb", "final"), mi.default_dest("fumi", "final"),
           mi.default_dest("fumi", "draft")) == ("kirim", "fumi", "draft"))
    check("junk is not queued: signatures, forwarded mails, zips",
          [a["filename"] for a in mi._attachments({"parts": [
              {"partId": str(i), "filename": n, "mimeType": "application/octet-stream",
               "body": {"attachmentId": f"x{i}", "size": 90_000}}
              for i, n in enumerate(["image001.png", "RE: AMJ30.eml", "photos.zip", "PEB 30.pdf"])]})]
          == ["PEB 30.pdf"])
    check("a Word file in a Fumi thread is the Fumi draft",
          mi._guess_from_name("PT. NMEHMOOD 0788CN.docx", "fumi")
          == {"doc_type": "fumi", "stage": "draft", "correction": False})
    check("a .docx falls back to the file name",
          mi._guess_from_name("TTJ DRAF PHYTO 08102026 KUALANAMU.docx")
          == {"doc_type": "phyto", "stage": "draft", "correction": False})

    personal = message("m3", "friend@example.com", "edbertongko88@gmail.com", "Holiday photos",
                       ["beach.pdf"], thread="t9")
    check("mail outside document threads is ignored", mi.process(db, gmail, personal) == []
          and not db.query("SELECT 1 FROM mail_queue WHERE thread_id='t9'"))

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Mail inbox OK - all checks passed.")
