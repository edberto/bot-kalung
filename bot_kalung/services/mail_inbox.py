"""Email reader + approval queue (doc automation Phase 2).

The bot READS the signed-in Gmail (tools/google_signin.py) — it never sends.

  Threads   tied to a shipment by subject: "{exporter}{seq}" (AMI/PMA: their
            own number, "AMI035") or, for BL, the DO number. The other party
            says what the thread is for: docexim.sbm -> PEB/COO/Phyto,
            pestcindo1 / gucimas_p17 -> Fumi, cintya.astryani@aca -> Insurance.
            Mail outside such threads is ignored.
  Team      (ongkalung, edbertongko88, achentan96) sending SI + VGM (Fumi: SI)
            -> those documents become Dikerjakan.
  Others    every attachment goes to the queue (`mail_queue`), with Claude's
            guess at the document type and draft/final, the target folder and
            the name "{EX}{seq}-{Doc}-{original}". After a document was OK'd, a
            new version is a correction: NOTUL- if the team asked for it in the
            thread, else REV-.
  Approve   in the PWA -> `save_approved` puts the file in the shipment folder
            (as the signed-in user; the bot's own Drive access is read-only),
            unless the same file is already there, then re-checks that item.
"""

from __future__ import annotations

import base64
import email.utils
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import doc_checks, doc_status

log = logging.getLogger(__name__)

TEAM = {"ongkalung@gmail.com", "edbertongko88@gmail.com", "achentan96@gmail.com"}
KIND_BY_ADDRESS = {
    "docexim.sbm@gmail.com": "peb",
    "pestcindo1@gmail.com": "fumi",
    "gucimas_p17@yahoo.co.id": "fumi",
    "cintya.astryani@aca.co.id": "insurance",
}
KIND_CODES = {"peb": ("peb_npe", "coo", "phyto"), "fumi": ("fumi",),
              "insurance": ("insurance",), "bl": ("bl",)}
# Document type -> (action item, name label)
DOC_TYPES = {"peb": ("peb_npe", "PEB"), "npe": ("peb_npe", "NPE"), "coo": ("coo", "COO"),
             "phyto": ("phyto", "Phyto"), "fumi": ("fumi", "Fumi"), "bl": ("bl", "BL"),
             "insurance": ("insurance", "Insurance"), "other": (None, "Dok")}
DEST_KINDS = ("draft", "kirim", "fumi", "peb")       # doc_status.folder_kind values
FIRST_RUN_DAYS = 1           # first run only; after that, mail since the last poll
_SMALL_IMAGE = 30_000          # logos / signatures in mail bodies
# Documents come as PDF, scans/photos, or Word (a Fumi / Phyto draft). Not
# forwarded mails (.eml), photo bundles (.zip), or "image001.png" signatures.
_DOC_FILE = re.compile(r"(?i)\.(pdf|jpe?g|png|docx?)$")
_SIGNATURE = re.compile(r"(?i)^image\d{3}\.(png|jpe?g|gif)$")
# A file Claude can't read (Word) or couldn't place: the thread says which document.
_KIND_DOC = {"fumi": "fumi", "insurance": "insurance", "bl": "bl"}
_ROOT = Path(__file__).resolve().parents[2]


# -- Google clients (the user's sign-in) ---------------------------------------------
def _credentials():
    from google.oauth2.credentials import Credentials

    env = dict(os.environ)
    if not env.get("GOOGLE_OAUTH_REFRESH_TOKEN"):
        local = _ROOT / "secrets" / "google-oauth-token.json"
        if not local.is_file():
            return None
        env.update(json.loads(local.read_text()))
    return Credentials(None, refresh_token=env["GOOGLE_OAUTH_REFRESH_TOKEN"],
                       client_id=env["GOOGLE_OAUTH_CLIENT_ID"],
                       client_secret=env["GOOGLE_OAUTH_CLIENT_SECRET"],
                       token_uri="https://oauth2.googleapis.com/token")


def clients():
    """(gmail, drive) as the signed-in user, or (None, None) when not set up."""
    from googleapiclient.discovery import build

    creds = _credentials()
    if creds is None:
        return None, None
    return (build("gmail", "v1", credentials=creds, cache_discovery=False),
            build("drive", "v3", credentials=creds, cache_discovery=False))


# -- reading mail ----------------------------------------------------------------------
def _headers(msg) -> dict:
    return {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}


def _addresses(*values) -> set[str]:
    return {a.lower() for _, a in email.utils.getaddresses([v for v in values if v]) if a}


def _attachments(payload) -> list[dict]:
    """[{part, filename, mime, size}] — files, not the mail body or tiny inline images."""
    out = []

    def walk(p):
        for sub in p.get("parts", []) or []:
            walk(sub)
        body = p.get("body", {})
        name = p.get("filename") or ""
        if name and body.get("attachmentId") and _DOC_FILE.search(name) and not _SIGNATURE.match(name):
            small_image = p.get("mimeType", "").startswith("image/") and body.get("size", 0) < _SMALL_IMAGE
            if not small_image:
                out.append({"part": p.get("partId"), "filename": p["filename"],
                            "mime": p.get("mimeType", ""), "size": body.get("size", 0),
                            "attachment_id": body["attachmentId"]})
    walk(payload)
    return out


def _attachment_bytes(gmail, message_id: str, part_id: str) -> bytes:
    """An attachment by its part id (Gmail's attachmentId changes per fetch)."""
    msg = gmail.users().messages().get(userId="me", id=message_id, format="full").execute(num_retries=5)
    att = next(a for a in _attachments(msg["payload"]) if a["part"] == part_id)
    raw = gmail.users().messages().attachments().get(
        userId="me", messageId=message_id, id=att["attachment_id"]).execute(num_retries=5)["data"]
    return base64.urlsafe_b64decode(raw)


def _norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def match_shipment(db, subject: str):
    """(shipment row, "subject" | "do") for an email subject, or (None, None).
    "AMJ30", "AMI035-4X40", "NIT21-10x40", "GGN03"; else the DO/booking number."""
    active = db.query("SELECT id, exporter_code, sequence_number, doc_seq, booking_number "
                      "FROM shipments WHERE status='active'")
    for code, n in re.findall(r"(?<![A-Z])([A-Z]{2,5})\s?-?\s?(\d{1,3})(?!\d)", (subject or "").upper()):
        for s in active:
            if s["exporter_code"] == code and int(n) == (s["doc_seq"] or s["sequence_number"]):
                return s, "subject"
    subj = _norm(subject)
    for s in active:
        b = _norm(s["booking_number"])
        if len(b) >= 6 and b in subj:
            return s, "do"
    return None, None


# -- what an attachment is ---------------------------------------------------------------
_CLASSIFY_PROMPT = """This file was emailed to an Indonesian exporter's shipping team.
What is it? doc_type: "peb" (PEB, Pemberitahuan Ekspor Barang), "npe" (NPE,
Nota Pelayanan Ekspor), "coo" (certificate of origin), "phyto" (phytosanitary
certificate), "fumi" (fumigation certificate), "bl" (bill of lading / sea
waybill), "insurance" (insurance policy / certificate), or "other".
stage: "draft" (a draft / proof for checking) or "final" (issued, signed,
approved or original).
correction: true if it says it is a revision, amendment or correction (e.g.
"REVISED", "AMENDED", "NOTA PEMBETULAN")."""

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": list(DOC_TYPES)},
        "stage": {"type": "string", "enum": ["draft", "final"]},
        "correction": {"type": "boolean"},
    },
    "required": ["doc_type", "stage", "correction"],
    "additionalProperties": False,
}

_NAME_TYPES = (("npe", r"(?<![a-z])npe(?![a-z])"), ("peb", r"(?<![a-z])peb(?![a-z])"),
               ("phyto", r"phyto"), ("fumi", r"fumi"), ("coo", r"(?<![a-z])coo(?![a-z])"),
               ("insurance", r"(?<![a-z])(ins|insurance|polis)(?![a-z])"),
               ("bl", r"(?<![a-z])(o?bl|swb)(?![a-z])|bill of lading"))


def _guess_from_name(filename: str, kind: str | None = None) -> dict:
    name = filename.lower()
    doc = next((t for t, rx in _NAME_TYPES if re.search(rx, name)), _KIND_DOC.get(kind, "other"))
    # a Word file is something still being edited — a draft
    stage = "draft" if re.search(r"dra(f|ft)|proof|\.docx?$", name) else "final"
    return {"doc_type": doc, "stage": stage, "correction": bool(re.search(r"(?<![a-z])rev", name))}


def _ask_classify(data: bytes, mime: str) -> dict:
    import anthropic

    if mime == "application/pdf":
        block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                "data": base64.standard_b64encode(data).decode()}}
    else:
        block = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.standard_b64encode(doc_checks._jpeg(data)).decode()}}
    resp = anthropic.Anthropic().beta.messages.create(
        model=doc_checks.MODEL, max_tokens=2000,
        betas=["server-side-fallback-2026-06-01"], fallbacks=[{"model": "claude-opus-4-8"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": _CLASSIFY_SCHEMA}},
        messages=[{"role": "user", "content": [block, {"type": "text", "text": _CLASSIFY_PROMPT}]}],
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"AI read stopped: {resp.stop_reason}")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def classify(db, data: bytes, att: dict, ask=_ask_classify, kind: str | None = None) -> dict:
    """Claude reads PDFs and images (cached per file content); anything else,
    or an undecided read, falls back to the file name and the thread (a Word
    file in a pestcindo thread is the Fumi draft)."""
    readable = att["mime"] == "application/pdf" or att["mime"].startswith("image/")
    if readable and len(data) <= doc_checks._MAX_PDF_BYTES:
        sha = hashlib.sha256(data).hexdigest()
        got = doc_checks._cached(db, f"mail:{sha}", "1", lambda: ask(data, att["mime"]),
                                 ask is _ask_classify)
        if got:
            return got          # Claude's "other" stands: BL threads carry tank certs, bills…
    return _guess_from_name(att["filename"], kind)


# -- team replies: OK / revisi from the email text --------------------------------------------
_REVIEWABLE = ("draft_received", "draft_revision", "draft_ok")
# Where the quoted thread starts. Gmail wraps "On Mon, Oct 12, 2026 at 10:00 X
# <x@y> wrote:" over two lines, so the match may span a line break.
_QUOTE_START = re.compile(r"(?ims)^(on\s.{0,300}?\swrote:|pada\s.{0,300}?\smenulis:"
                          r"|-----\s*original message|from: .+@)")
_PAPERWORK = re.compile(r"(?i)(?<![a-z])(si|vgm|inv|invoice|pl|p\.?\s?list|packing)(?![a-z])")

_REPLY_PROMPT = """A member of an Indonesian exporter's shipping team wrote this email in a
thread about these documents of one shipment: {docs}.
Attachments on this email: {attachments}.

For each document, did the writer APPROVE the latest draft ("ok", "sudah benar",
"lanjut", "approved", "silakan final"), ask for CHANGES to it (corrections,
"tolong ganti / revisi / salah", or an attached marked-up copy of that document),
or NEITHER (not about it, a question, just forwarding)? Only say ok / revise when
the text clearly refers to that document — or, with a single document in the
thread, clearly to the draft. When unsure, "none". "quote": the few words that
decided it (empty for none).

Email text:
{text}"""

_REPLY_SCHEMA = {
    "type": "object",
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object",
        "properties": {"doc": {"type": "string"},
                       "verdict": {"type": "string", "enum": ["ok", "revise", "none"]},
                       "quote": {"type": "string"}},
        "required": ["doc", "verdict", "quote"], "additionalProperties": False}}},
    "required": ["verdicts"], "additionalProperties": False,
}


def _body_text(payload) -> str:
    """The new text of an email: its plain-text part without the quoted thread."""
    def parts(p):
        yield p
        for sub in p.get("parts", []) or []:
            yield from parts(sub)
    plain = next((p for p in parts(payload) if p.get("mimeType") == "text/plain"
                  and p.get("body", {}).get("data")), None)
    html = next((p for p in parts(payload) if p.get("mimeType") == "text/html"
                 and p.get("body", {}).get("data")), None)
    part = plain or html
    if part is None:
        return ""
    text = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
    if part is html:
        text = re.sub(r"(?s)<(style|script).*?</\1>|<[^>]+>", " ", text)
    m = _QUOTE_START.search(text)
    text = text[:m.start()] if m else text
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(">"))
    return re.sub(r"[ \t]+", " ", text).strip()[:3000]


def _ask_reply(text: str, docs: dict, attachments: list[str]) -> dict:
    import anthropic

    prompt = _REPLY_PROMPT.format(
        docs=", ".join(f'"{code}" ({label})' for code, label in docs.items()),
        attachments=", ".join(attachments) or "none", text=text or "(no text)")
    resp = anthropic.Anthropic().beta.messages.create(
        model=doc_checks.MODEL, max_tokens=2000,
        betas=["server-side-fallback-2026-06-01"], fallbacks=[{"model": "claude-opus-4-8"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": _REPLY_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"AI read stopped: {resp.stop_reason}")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def apply_reply(db, msg, shipment, kind: str, sender: str, sent_at: str, atts: list,
                ask=_ask_reply) -> list[str]:
    """A team reply that approves or asks for changes moves the document(s) it
    is about: -> Draft OK, or -> Draf direvisi (also back from Draft OK: a
    change asked after the OK). Only documents at a draft stage, and only when
    the reply is newer than the item's last status change; never from or to
    Final. The email is kept as the evidence (mail_* columns)."""
    rows = {r["code"]: r for r in db.query(
        "SELECT id, code, title, status, updated_at FROM action_items WHERE shipment_id=?",
        (shipment["id"],)) if r["code"] in KIND_CODES[kind] and r["status"] in _REVIEWABLE}
    docs_attached = [a["filename"] for a in atts if not _PAPERWORK.search(a["filename"])]
    text = _body_text(msg["payload"])
    if not rows or not (text or docs_attached):
        return []
    got = doc_checks._cached(
        db, f"reply:{msg['id']}", "1",
        lambda: ask(text, {c: r["title"] for c, r in rows.items()}, docs_attached),
        ask is _ask_reply)
    report, label = [], f"{shipment['exporter_code']}{shipment['sequence_number']}"
    who = sender.split("@")[0]
    for v in (got or {}).get("verdicts", []):
        row = rows.get(v.get("doc"))
        if row is None or v["verdict"] == "none":
            continue
        if sent_at <= (row["updated_at"] or "").replace(" ", "T")[:19]:
            continue                       # something newer already set this status
        new = {"ok": "draft_ok", "revise": "draft_revision"}[v["verdict"]]
        if new == row["status"] or (new == "draft_ok" and row["status"] == "draft_ok"):
            continue
        quote = (v.get("quote") or "").strip()[:120]
        db.execute("UPDATE action_items SET status=?, updated_at=?, mail_status=?, mail_evidence=?, "
                   "mail_evidence_ref=?, mail_at=? WHERE id=?",
                   (new, sent_at, new, f"{who}: {quote}" if quote else who, msg["threadId"],
                    sent_at, row["id"]))
        report.append(f"{label}: {row['title']} -> {'Draft OK' if new == 'draft_ok' else 'Draf direvisi'}"
                      f" (email {who}: {quote})")
    return report


# -- naming and placement --------------------------------------------------------------------
def default_dest(doc_type: str, stage: str) -> str:
    if stage == "draft":
        return "draft"
    return "fumi" if doc_type == "fumi" else "kirim"


def file_name(shipment, doc_type: str, stage: str, prefix: str, original: str) -> str:
    """The attachment's own name, unchanged (the user's call). What the file is
    — for the status rules — is recorded instead (mail_queue.saved_ref +
    doc_type, doc_status.with_kinds)."""
    return original


# -- the poll -------------------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _sent_at(msg) -> str:
    return datetime.fromtimestamp(int(msg["internalDate"]) / 1000, timezone.utc) \
        .replace(tzinfo=None).isoformat(timespec="seconds")


def _relevant_query(db, after: int) -> str:
    """Gmail search for mail that can matter; the rest of the inbox is never read.
      - with attachments: from/to the parties or the team, or an active DO
        number in the subject (BL threads come from varying senders);
      - from the team to a party, or about a DO, even without attachments —
        a text reply ("ok lanjut", "tolong ganti …") sets OK / revisi."""
    parties = sorted(KIND_BY_ADDRESS)
    dos = sorted({_norm(r["booking_number"]) for r in db.query(
        "SELECT booking_number FROM shipments WHERE status='active' AND booking_number IS NOT NULL")
        if len(_norm(r["booking_number"])) >= 6})
    anyone = [f"(from:{p} OR to:{p} OR cc:{p})" for p in sorted(TEAM | set(parties))]
    about = [f"(to:{p} OR cc:{p})" for p in parties] + [f"subject:{d}" for d in dos]
    subjects = [f"subject:{d}" for d in dos]
    from_team = "{" + " ".join(f"from:{p}" for p in sorted(TEAM)) + "}"
    with_files = "(has:attachment {" + " ".join(anyone + subjects) + "})"
    replies = f"({from_team} {{{' '.join(about)}}})"
    # -in:scheduled: a scheduled send carries its future send time
    return f"after:{after} -in:scheduled {{{with_files} {replies}}}"


def _gentle(call, tries: int = 4):
    """A Gmail call that waits and retries when the per-minute quota is hit."""
    import time

    from googleapiclient.errors import HttpError

    for attempt in range(tries):
        try:
            return call()
        except HttpError as exc:
            if exc.resp.status not in (403, 429) or attempt == tries - 1:
                raise
            time.sleep(30 * (attempt + 1))


def drive_lookup(drive):
    """in_drive(shipment, md5) -> True when that file is already somewhere in the
    shipment folder (top level or a subfolder), by content. One listing per
    shipment per poll."""
    cache: dict = {}

    def md5s(folder_id: str) -> set:
        out, folders = set(), [folder_id]
        for depth in range(2):
            nxt = []
            for fid in folders:
                for f in drive.files().list(
                        q=f"'{fid}' in parents and trashed=false", pageSize=1000,
                        fields="files(id,mimeType,md5Checksum)", supportsAllDrives=True,
                        includeItemsFromAllDrives=True).execute().get("files", []):
                    if f["mimeType"] == "application/vnd.google-apps.folder":
                        nxt.append(f["id"])
                    elif f.get("md5Checksum"):
                        out.add(f["md5Checksum"])
            folders = nxt
        return out

    def in_drive(shipment, md5: str) -> bool:
        ref = (shipment["folder_path"] or "") if shipment else ""
        if not ref.startswith("drive:"):
            return False
        if ref not in cache:
            cache[ref] = md5s(ref.split(":", 1)[1])
        return md5 in cache[ref]
    return in_drive


def poll(db, gmail, ask=_ask_classify, drive=None) -> list[str]:
    """Read mail since the last poll (first run: the last FIRST_RUN_DAYS days).
    Returns report lines."""
    from ..core.settings import Settings

    settings = Settings(db)
    now = int(datetime.now(timezone.utc).timestamp())
    # never "read up to" a moment in the future (it would skip mail arriving
    # before it); heals a value set before that was guarded
    after = min(int(settings.get("mail_after") or 0) or now - FIRST_RUN_DAYS * 86400, now - 900)
    ids, token = [], None
    query = _relevant_query(db, after)
    while True:
        resp = gmail.users().messages().list(userId="me", q=query, pageToken=token,
                                             maxResults=500).execute(num_retries=5)
        ids += [m["id"] for m in resp.get("messages", [])]
        token = resp.get("nextPageToken")
        if not token:
            break
    seen = {r["message_id"] for r in db.query("SELECT message_id FROM mail_seen")}
    report, newest = [], after
    msgs = []
    for mid in ids:
        if mid in seen:
            continue
        msgs.append(_gentle(lambda: gmail.users().messages().get(
            userId="me", id=mid, format="full").execute(num_retries=5)))
    in_drive = drive_lookup(drive) if drive is not None else None
    for msg in sorted(msgs, key=lambda m: int(m["internalDate"])):
        try:
            report += process(db, gmail, msg, ask, in_drive)
        except Exception as exc:      # noqa: BLE001 - one bad mail must not stop the rest
            log.warning("mail %s failed: %s", msg["id"], exc)
            continue
        newest = max(newest, int(msg["internalDate"]) // 1000)
    # 15 minutes of overlap so nothing falls between two polls; mail_seen keeps
    # a message from being handled twice
    settings.set("mail_after", str(max(after, min(newest, now) - 900)))
    return report


def process(db, gmail, msg, ask=_ask_classify, in_drive=None, reply_ask=_ask_reply) -> list[str]:
    h = _headers(msg)
    sender = email.utils.parseaddr(h.get("from", ""))[1].lower()
    team = sender in TEAM
    thread_id, sent_at, subject = msg["threadId"], _sent_at(msg), h.get("subject", "")
    db.execute("INSERT INTO mail_seen (message_id, thread_id, from_team, sent_at) VALUES (?,?,?,?) "
               "ON CONFLICT (message_id) DO NOTHING", (msg["id"], thread_id, 1 if team else 0, sent_at))

    thread = db.query_one("SELECT shipment_id, kind FROM mail_threads WHERE thread_id=?", (thread_id,))
    shipment_id, kind = (thread["shipment_id"], thread["kind"]) if thread else (None, None)
    if not shipment_id:
        s, via = match_shipment(db, subject)
        shipment_id = s["id"] if s else None
        kind = kind or ("bl" if via == "do" else None)
    kind = kind or next((KIND_BY_ADDRESS[a] for a in _addresses(h.get("from"), h.get("to"), h.get("cc"))
                         if a in KIND_BY_ADDRESS), None)
    if not shipment_id and not kind:
        return []                                  # not a document thread
    db.execute("INSERT INTO mail_threads (thread_id, shipment_id, kind) VALUES (?,?,?) "
               "ON CONFLICT (thread_id) DO UPDATE SET shipment_id=COALESCE(excluded.shipment_id, "
               "mail_threads.shipment_id), kind=COALESCE(excluded.kind, mail_threads.kind)",
               (thread_id, shipment_id, kind))
    shipment = shipment_id and db.query_one("SELECT * FROM shipments WHERE id=?", (shipment_id,))
    label = f"{shipment['exporter_code']}{shipment['sequence_number']}" if shipment else "?"
    atts = _attachments(msg["payload"])
    report = []

    if team:
        names = " ".join(a["filename"] for a in atts)
        has_si = re.search(r"(?i)(?<![a-z])si(?![a-z])", names)
        has_vgm = re.search(r"(?i)vgm", names)
        if shipment and kind and has_si and (has_vgm or kind == "fumi"):
            for code in KIND_CODES[kind]:
                row = db.query_one("SELECT id, title, status FROM action_items "
                                   "WHERE shipment_id=? AND code=?", (shipment_id, code))
                if row and row["status"] == "pending":
                    # the email is the evidence (✉️ line, status history)
                    db.execute("UPDATE action_items SET status='in_progress', updated_at=?, "
                               "mail_status='in_progress', mail_evidence=?, mail_evidence_ref=?, "
                               "mail_at=? WHERE id=?",
                               (sent_at, f"{sender.split('@')[0]}: email '{subject}' (SI/VGM)",
                                thread_id, sent_at, row["id"]))
                    report.append(f"{label}: {row['title']} -> Dikerjakan (email '{subject}')")
        elif shipment and kind:
            report += apply_reply(db, msg, shipment, kind, sender, sent_at, atts, reply_ask)
        return report

    for att in atts:
        if db.query_one("SELECT 1 FROM mail_queue WHERE message_id=? AND part_id=?",
                        (msg["id"], att["part"])):
            continue
        data = base64.urlsafe_b64decode(gmail.users().messages().attachments().get(
            userId="me", messageId=msg["id"], id=att["attachment_id"]).execute(num_retries=5)["data"])
        sha = hashlib.sha256(data).hexdigest()
        if shipment_id and db.query_one("SELECT 1 FROM mail_queue WHERE sha256=? AND shipment_id=?",
                                        (sha, shipment_id)):
            continue                               # the same file sent again
        if shipment and in_drive and in_drive(shipment, hashlib.md5(data).hexdigest()):
            continue                               # already saved in the shipment folder
        guess = classify(db, data, att, ask, kind)
        doc_type, stage = guess["doc_type"], guess["stage"]
        code = DOC_TYPES[doc_type][0]
        prefix = ""                                # REV / NOTUL: the team adds those by hand
        auto = bool(shipment_id) and doc_type != "other"
        db.execute(
            "INSERT INTO mail_queue (id, created_at, message_id, thread_id, part_id, filename, mime, "
            "size, sha256, shipment_id, subject, sender, sent_at, doc_type, stage, prefix, dest, "
            "proposed_name, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{msg['id']}:{att['part']}", _now(), msg["id"], thread_id, att["part"], att["filename"],
             att["mime"], len(data), sha, shipment_id, subject, sender, sent_at, doc_type, stage,
             prefix, default_dest(doc_type, stage),
             file_name(shipment, doc_type, stage, prefix, att["filename"]),
             # A known document of a known shipment is saved straight away (the
             # user sorts the Drive out); anything else waits in Kotak Masuk.
             "approved" if auto else "pending"))
        report.append(f"{label}: {att['filename']} -> "
                      f"{'disimpan' if auto else 'Kotak Masuk'} ({doc_type}, {stage})")
    return report


# -- saving approved entries -------------------------------------------------------------------------
def unique_name(name: str, taken: set) -> str:
    """`name`, or "name-Copy.pdf", "name-Copy 2.pdf", … — never overwrite."""
    if name not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    stem, ext = (stem, dot + ext) if dot else (name, "")
    n = 1
    while True:
        candidate = f"{stem}-Copy{'' if n == 1 else f' {n}'}{ext}"
        if candidate not in taken:
            return candidate
        n += 1


def already_saved(entry, there: list, md5: str, name: str) -> str | None:
    """The file in the folder that makes saving pointless, or None: the same
    content, or the same name (the bot's or the attachment's own) added AFTER
    the email arrived — someone already saved it by hand."""
    for f in there:
        if f.get("md5Checksum") == md5:
            return f["name"]
        later = (f.get("createdTime") or "")[:19] > (entry["sent_at"] or "")
        if later and f["name"] in (name, entry["filename"]):
            return f["name"]
    return None


def save_approved(db, gmail, drive, folder_resolver, check=None, notify=None) -> list[str]:
    """Put each approved entry into its shipment folder, as the signed-in user.
    Already there (already_saved): the entry is dropped. A different, older
    file with the same name: saved as "…-Copy". Then `check(shipment_id,
    codes)` refreshes that document, and `notify(shipment, [(doc, where)])`
    tells the team, once per shipment."""
    from googleapiclient.http import MediaInMemoryUpload

    report, saved = [], {}
    for e in db.query("SELECT * FROM mail_queue WHERE status='approved'"):
        status, note = "error", None
        try:
            s = e["shipment_id"] and db.query_one("SELECT * FROM shipments WHERE id=?", (e["shipment_id"],))
            if not s or not s["folder_path"]:
                raise RuntimeError("pengiriman/folder tidak ditemukan")
            folder = folder_resolver(s["folder_path"])
            sub = next((d for d in folder.iterdir()
                        if d.is_dir() and doc_status.folder_kind(d.name) == e["dest"]), None)
            if sub is None:
                raise RuntimeError(f"subfolder '{e['dest']}' tidak ada di folder pengiriman")
            data = _attachment_bytes(gmail, e["message_id"], e["part_id"])
            name = file_name(s, e["doc_type"], e["stage"], e["prefix"] or "", e["filename"])
            md5 = hashlib.md5(data).hexdigest()
            there = drive.files().list(q=f"'{sub.id}' in parents and trashed=false",
                                       fields="files(name,md5Checksum,createdTime)", pageSize=1000,
                                       supportsAllDrives=True, includeItemsFromAllDrives=True
                                       ).execute().get("files", [])
            dup = already_saved(e, there, md5, name)
            if dup:          # already in the folder: nothing to keep in the queue
                db.execute("DELETE FROM mail_queue WHERE id=?", (e["id"],))
                report.append(f"{e['filename']}: sudah ada ({dup}) — dihapus dari antrean")
                continue
            else:
                name = unique_name(name, {f["name"] for f in there})
                made = drive.files().create(body={"name": name, "parents": [sub.id]},
                                            media_body=MediaInMemoryUpload(data, mimetype=e["mime"] or None),
                                            fields="id", supportsAllDrives=True).execute()
                db.execute("UPDATE mail_queue SET saved_ref=? WHERE id=?",
                           (f"drive:{made['id']}", e["id"]))
                status, note = "saved", f"{sub.name}/{name}"
                saved.setdefault(s["id"], (s, []))[1].append(
                    (DOC_TYPES.get(e["doc_type"], DOC_TYPES["other"])[1], note))
                code = DOC_TYPES.get(e["doc_type"], (None,))[0]
                if check and code:
                    check(s["id"], {code})
        except Exception as exc:      # noqa: BLE001 - shown on the entry
            note = str(exc)[:300]
        db.execute("UPDATE mail_queue SET status=?, note=?, decided_at=? WHERE id=?",
                   (status, note, _now(), e["id"]))
        report.append(f"{e['filename']}: {status}{' — ' + note if note else ''}")
    if notify:
        for s, files in saved.values():
            notify(s, files)
    return report
