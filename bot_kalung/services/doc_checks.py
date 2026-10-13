"""SI/VGM checklist items that need AI reading (doc automation 1C, step 2).

  do   "Draft awal (DO)": the DO / booking confirmation in the shipment folder's
       top level (or DO & CC/) matches the workbook — DO number vs booking no.,
       party, POD, vessel/voyage. Not ETD: the BL date is the source of truth.
  atd  "ATD (BL)": once the BL is in Dok kirim, its laden-on-board date matches
       every ETD/ATD cell on the SI / INV / PL sheets; before that, "BL belum
       diterima".

DOs and BLs are often scans (OOCL's text layer is garbled, most final BLs are
scanned), so the PDF goes to Claude, which reads the page images. Each read is
cached per file in `ai_reads` (keyed by Drive modifiedTime), and a scan makes at
most `MAX_READS_PER_SCAN` new reads. No ANTHROPIC_API_KEY -> no reads; a check
that can't be decided returns None and the caller keeps the previous one.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import date, datetime, timezone
from difflib import SequenceMatcher

from . import doc_status

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
MAX_READS_PER_SCAN = 20
_MAX_PDF_BYTES = 20 * 1024 * 1024
_left = MAX_READS_PER_SCAN

_BATAL = re.compile(r"(?i)batal")
_DO_NAME = re.compile(r"(?i)(?<![a-z])(do|bc|ro|booking|release)(?![a-z])")
_BOOKING_LIKE = re.compile(r"\d{8,}|[A-Z]{3,}\d{6,}")

_PROMPT = """This is a shipping document from an Indonesian exporter (Belawan port).
Classify it and read its fields. doc_type:
- "delivery_order": a carrier's release order / delivery order / D/O / booking
  confirmation / booking receipt notice / container release order / e-booking.
- "bill_of_lading": a bill of lading or sea waybill (draft, original, copy or scan).
- "other": anything else (invoice, permit, COPARN, receipt, ...).
Fields (null when the document doesn't show it):
- number: the DO / release / booking number; for a BL, the BL number.
- booking_number: the booking number if shown separately.
- quantity, size: container count and size, size normalised as 20GP / 40GP /
  40HC / 45HC / 20RF / 40RF (HQ and "high cube" are HC; 45G1 is 40HC).
- pod: port of discharge (or final destination if no POD), as a place name, not
  a code.
- vessel, voyage: the (first/ocean) vessel name and its voyage.
- etd: estimated departure / sailing date, YYYY-MM-DD.
- on_board_date: for a BL, the "laden on board" / "shipped on board" date,
  YYYY-MM-DD.
- issue_date: for a BL, the date of issue, YYYY-MM-DD."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": ["delivery_order", "bill_of_lading", "other"]},
        **{k: {"type": ["string", "null"]} for k in (
            "number", "booking_number", "size", "pod", "vessel", "voyage", "etd",
            "on_board_date", "issue_date")},
        "quantity": {"type": ["integer", "null"]},
    },
    "required": ["doc_type", "number", "booking_number", "quantity", "size", "pod",
                 "vessel", "voyage", "etd", "on_board_date", "issue_date"],
    "additionalProperties": False,
}


def new_scan() -> None:
    """Reset the per-scan budget of new AI reads."""
    global _left
    _left = MAX_READS_PER_SCAN


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _ask_claude(pdf: bytes) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-06-01"],
        fallbacks=[{"model": "claude-opus-4-8"}],
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf).decode()}},
            {"type": "text", "text": _PROMPT}]}],
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"AI read stopped: {resp.stop_reason}")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


class AiReader:
    """A DocFile's fields as read by Claude, through the `ai_reads` cache."""

    def __init__(self, db, fetch, ask=_ask_claude):
        self.db, self.fetch, self.ask = db, fetch, ask

    def __call__(self, f: doc_status.DocFile) -> dict | None:
        global _left
        if not f.ref or not f.name.lower().endswith(".pdf"):
            return None
        row = self.db.query_one("SELECT modified, data FROM ai_reads WHERE file_ref=?", (f.ref,))
        if row is not None and row["modified"] == f.modified:
            return json.loads(row["data"])
        if _left <= 0 or (self.ask is _ask_claude and not os.environ.get("ANTHROPIC_API_KEY")):
            return None
        _left -= 1
        try:
            pdf = self.fetch(f.ref)
            if len(pdf) > _MAX_PDF_BYTES:
                return None
            data = self.ask(pdf)
        except Exception as exc:      # noqa: BLE001 - try again next scan
            log.warning("AI read failed for %s: %s", f.name, exc)
            return None
        self.db.execute(
            "INSERT INTO ai_reads (file_ref, modified, data, read_at) VALUES (?,?,?,?) "
            "ON CONFLICT (file_ref) DO UPDATE SET modified=excluded.modified, "
            "data=excluded.data, read_at=excluded.read_at",
            (f.ref, f.modified, json.dumps(data, ensure_ascii=False), _now()))
        return data


# -- comparison helpers --------------------------------------------------------
def _norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _same_number(a, b) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a == b or a.endswith(b) or b.endswith(a))   # ONEYMESG… vs MESG…


def _size(s) -> str | None:
    """"40'HC" / "40 HIGH CUBE" / "40HQ" -> "40HC"; "40'" / "40GP" -> "40GP"."""
    t = str(s or "").upper()
    m = re.search(r"(20|40|45)", t)
    if not m:
        return None
    return m.group(1) + ("HC" if re.search(r"HC|HQ|HIGH|45G1", t) else
                         "RF" if re.search(r"RF|REEF", t) else "GP")


def _similar(a, b, cutoff=0.8) -> bool:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return True                    # nothing to compare
    return a in b or b in a or SequenceMatcher(None, a, b).ratio() >= cutoff


def _same_voyage(a, b) -> bool:
    a, b = _norm(a).lstrip("0"), _norm(b).lstrip("0")
    return not a or not b or a in b or b in a


def _iso(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _fmt(d: date) -> str:
    return d.strftime("%d %b %Y")


def _file(f) -> dict:
    return {"name": f.name, "ref": f.ref}


# -- the checks -----------------------------------------------------------------
def _do_candidates(files, booking) -> list:
    """PDFs in the folder's top level or DO & CC/ (HAI, JMI) that may be the
    DO, newest first: named after the booking,
    a DO word, or a booking-like number. Claude confirms which really is one."""
    b = _norm(booking)
    out = [f for f in files if f.folder in ("root", "do_cc") and f.name.lower().endswith(".pdf")
           and not _BATAL.search(f.name)
           and ((b and b in _norm(f.name)) or _DO_NAME.search(f.name)
                or _BOOKING_LIKE.search(f.name.upper()))]
    out.sort(key=lambda f: ((b and b in _norm(f.name)) or False, f.created), reverse=True)
    return out


def _bls(files) -> list:
    """BLs in Dok kirim, newest first."""
    return sorted((f for f in files if f.folder == "kirim" and doc_status.BL_FINAL.search(f.name)
                   and f.name.lower().endswith(".pdf")), key=lambda f: f.created, reverse=True)


def do_check(files, fields, read) -> dict | None:
    """The DO against the workbook. ETD is not compared: the DO only gives the
    initial value — the BL on-board date (atd_check) is the one that counts."""
    head = {"key": "do", "label": "Draft awal (DO)"}
    found = None
    for f in _do_candidates(files, fields.booking_number):
        data = read(f)
        if data is None:
            return None               # undecided (no key / budget / error) — keep the old check
        if data.get("doc_type") == "delivery_order":
            found = (f, data)
            break
    if found is None:
        return {**head, "done": False, "notes": ["DO tidak ditemukan"]}
    f, d = found
    notes = []
    if fields.booking_number and not (_same_number(d.get("number"), fields.booking_number)
                                      or _same_number(d.get("booking_number"),
                                                      fields.booking_number)):
        notes.append(f"Nomor DO {d.get('number') or '?'} ≠ booking Excel {fields.booking_number}")
    if d.get("quantity") and fields.container_quantity and (
            d["quantity"] != fields.container_quantity
            or (_size(d.get("size")) and _size(fields.container_size_short)
                and _size(d.get("size")) != _size(fields.container_size_short))):
        notes.append(f"Party DO {d['quantity']}x{d.get('size') or '?'} ≠ Excel "
                     f"{fields.container_quantity}x{fields.container_size_short or '?'}")
    if fields.destination_port and not _similar(d.get("pod"), fields.destination_port):
        notes.append(f"POD DO {d['pod']} ≠ Excel {fields.destination_port}")
    if fields.vessel_name and not _similar(d.get("vessel"), fields.vessel_name, 0.85):
        notes.append(f"Kapal DO {d['vessel']} ≠ Excel {fields.vessel_name}")
    if fields.voyage and not _same_voyage(d.get("voyage"), fields.voyage):
        notes.append(f"Voyage DO {d['voyage']} ≠ Excel {fields.voyage}")
    return {**head, "done": not notes, "notes": notes, "file": _file(f)}


def atd_check(files, fields, read) -> dict | None:
    head = {"key": "atd", "label": "ATD (BL)"}
    bls = _bls(files)
    if not bls:
        return {**head, "done": False, "notes": ["BL belum diterima"]}
    f = bls[0]
    data = read(f)
    if data is None:
        return None
    bl_date = _iso(data.get("on_board_date")) or _iso(data.get("issue_date"))
    if bl_date is None:
        return {**head, "done": False, "notes": ["BL: tanggal on board tidak terbaca"],
                "file": _file(f)}
    notes = []
    for sheet, raw, iso in fields.etd_cells or []:
        d = _iso(iso)
        if d is None:
            notes.append(f"{sheet}: ETD/ATD {raw or 'kosong'} — isi {_fmt(bl_date)}")
        elif d != bl_date:
            notes.append(f"{sheet}: ETD/ATD {_fmt(d)} ≠ BL {_fmt(bl_date)}")
    return {**head, "done": not notes, "notes": notes, "file": _file(f)}


def compute(db, folder, fields, previous: dict) -> list[dict]:
    """[do, atd] for a shipment folder; an undecidable check keeps `previous[key]`."""
    files = doc_status.list_files(folder)
    if not files:
        return [c for c in (previous.get("do"), previous.get("atd")) if c]
    read = AiReader(db, doc_status._fetcher(folder))
    out = []
    for key, check in (("do", do_check(files, fields, read)),
                       ("atd", atd_check(files, fields, read))):
        check = check if check is not None else previous.get(key)
        if check:
            out.append(check)
    return out
