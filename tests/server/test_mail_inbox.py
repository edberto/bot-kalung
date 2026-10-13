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
    db.execute("UPDATE action_items SET status='draft_ok', updated_at='2025-10-09T08:56:00', "
               "ok_at='2025-10-09T08:56:00' WHERE shipment_id=? AND code='peb_npe'", (amj,))
    cp = lambda code, stage: mi.correction_prefix(db, amj, code, stage, "t1", "2025-10-09T09:00:00")  # noqa: E731
    check("a draft after OK, nobody from the team asked -> REV", cp("peb_npe", "draft") == "REV")
    check("the first final after OK is the normal cycle -> no prefix", cp("peb_npe", "final") == "")
    check("before OK (revision loop) -> no prefix", cp("coo", "draft") == "")
    db.execute("INSERT INTO mail_seen (message_id, thread_id, from_team, sent_at) "
               "VALUES ('m9', 't1', 1, '2025-10-09T08:58:00')")
    check("a draft after OK that the team asked for (team mail since the OK) -> NOTUL",
          cp("peb_npe", "draft") == "NOTUL")
    db.execute("UPDATE action_items SET status='draft_revision' WHERE shipment_id=? AND code='peb_npe'", (amj,))
    check("...still NOTUL after the change request moved the status back to Draf direvisi",
          cp("peb_npe", "draft") == "NOTUL" and cp("peb_npe", "final") == "NOTUL")
    db.execute("UPDATE action_items SET status='draft_ok', ok_at=NULL WHERE shipment_id=? "
               "AND code='peb_npe'", (amj,))
    ActionItems(db).set_status(db.query_one("SELECT id FROM action_items WHERE shipment_id=? "
                                            "AND code='peb_npe'", (amj,))["id"], "final")
    check("reaching Draft OK or later records ok_at (set_status / record_auto / replies)",
          db.query_one("SELECT ok_at FROM action_items WHERE shipment_id=? AND code='peb_npe'",
                       (amj,))["ok_at"] is not None)
    db.execute("UPDATE action_items SET status='draft_ok', ok_at=NULL WHERE shipment_id=? "
               "AND code='peb_npe'", (amj,))

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

    pdfs.update({"m4-0": b"%PDF already"})
    again = message("m4", "Doc Exim <docexim.sbm@gmail.com>", "ongkalung@gmail.com", "Re: AMJ30",
                    ["PEB 30 ttd.pdf"], when="1760000200000")
    import hashlib
    have = hashlib.md5(b"%PDF already").hexdigest()
    mi.process(db, gmail, again, ask=lambda d, m: {"doc_type": "peb", "stage": "final", "correction": False},
               in_drive=lambda s, md5: md5 == have)
    check("a file already in the shipment folder is not queued",
          not db.query("SELECT 1 FROM mail_queue WHERE message_id='m4'"))

    # ---- team replies: OK / revisi from the text --------------------------------
    def text_msg(mid, sender, subject, text, when, thread="t1"):
        body = base64.urlsafe_b64encode(text.encode()).decode()
        return {"id": mid, "threadId": thread, "internalDate": when,
                "payload": {"headers": [{"name": "From", "value": sender},
                                        {"name": "To", "value": "docexim.sbm@gmail.com"},
                                        {"name": "Subject", "value": "Re: AMJ30"}],
                            "mimeType": "text/plain", "body": {"data": body}}}

    db.execute("UPDATE action_items SET status='draft_received', updated_at='2025-10-09T00:00:00' "
               "WHERE shipment_id=? AND code IN ('peb_npe','coo','phyto')", (amj,))
    asked = []
    verdicts = {"verdicts": [{"doc": "coo", "verdict": "ok", "quote": "COO ok"},
                             {"doc": "peb_npe", "verdict": "revise", "quote": "PEB tolong ganti berat"},
                             {"doc": "phyto", "verdict": "none", "quote": ""}]}
    reply = text_msg("r1", "Kevin <achentan96@gmail.com>", "Re: AMJ30",
                     "COO ok, PEB tolong ganti berat\n\nOn Fri, Doc Exim wrote:\n> lama", "1760100000000")
    mi.process(db, gmail, reply, reply_ask=lambda text, docs, att: asked.append(text) or verdicts)
    check("a team reply sets OK / revisi per document it names, leaves the rest",
          (status(amj, "coo"), status(amj, "peb_npe"), status(amj, "phyto"))
          == ("draft_ok", "draft_revision", "draft_received"))
    check("...reads only the new text, not the quoted thread", asked == ["COO ok, PEB tolong ganti berat"])
    ev = db.query_one("SELECT mail_status, mail_evidence, mail_evidence_ref FROM action_items "
                      "WHERE shipment_id=? AND code='peb_npe'", (amj,))
    check("...and keeps the email as evidence",
          (ev["mail_status"], ev["mail_evidence"], ev["mail_evidence_ref"])
          == ("draft_revision", "achentan96: PEB tolong ganti berat", "t1"))
    mi.process(db, gmail, reply, reply_ask=lambda *a: asked.append("again") or verdicts)
    old = text_msg("r2", "Kevin <achentan96@gmail.com>", "Re: AMJ30", "PEB ok", "1750000000000")
    mi.process(db, gmail, old, reply_ask=lambda *a: {"verdicts": [
        {"doc": "peb_npe", "verdict": "ok", "quote": "PEB ok"}]})
    check("an email older than the last status change changes nothing; the same email isn't read twice",
          status(amj, "peb_npe") == "draft_revision" and asked.count("again") == 0)
    db.execute("UPDATE action_items SET status='final' WHERE shipment_id=? AND code='coo'", (amj,))
    other = text_msg("r3", "Docexim <docexim.sbm@gmail.com>", "Re: AMJ30", "COO ok", "1760200000000")
    mi.process(db, gmail, other, reply_ask=lambda *a: {"verdicts": [
        {"doc": "coo", "verdict": "revise", "quote": "x"}]})
    check("replies from outside the team never set OK / revisi; a final document isn't touched",
          status(amj, "coo") == "final")
    q = mi._relevant_query(db, 1760000000)
    check("the search also fetches team mail without attachments (text replies)",
          q.startswith("after:1760000000 {(has:attachment") and "from:achentan96@gmail.com" in q
          and "(to:docexim.sbm@gmail.com OR cc:docexim.sbm@gmail.com)" in q)

    personal = message("m3", "friend@example.com", "edbertongko88@gmail.com", "Holiday photos",
                       ["beach.pdf"], thread="t9")
    check("mail outside document threads is ignored", mi.process(db, gmail, personal) == []
          and not db.query("SELECT 1 FROM mail_queue WHERE thread_id='t9'"))

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Mail inbox OK - all checks passed.")
