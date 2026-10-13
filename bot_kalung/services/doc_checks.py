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


def _cached(db, ref: str, modified: str, produce, live: bool) -> dict | None:
    """`produce()` (one Claude read) through the `ai_reads` cache. None when it
    can't be read now: no API key (`live` = real Claude), budget spent, error."""
    global _left
    row = db.query_one("SELECT modified, data FROM ai_reads WHERE file_ref=?", (ref,))
    if row is not None and row["modified"] == modified:
        return json.loads(row["data"])
    if _left <= 0 or (live and not os.environ.get("ANTHROPIC_API_KEY")):
        return None
    _left -= 1
    try:
        data = produce()
    except Exception as exc:      # noqa: BLE001 - try again next scan
        log.warning("AI read failed for %s: %s", ref, exc)
        return None
    if data is None:
        return None
    db.execute(
        "INSERT INTO ai_reads (file_ref, modified, data, read_at) VALUES (?,?,?,?) "
        "ON CONFLICT (file_ref) DO UPDATE SET modified=excluded.modified, "
        "data=excluded.data, read_at=excluded.read_at",
        (ref, modified, json.dumps(data, ensure_ascii=False), _now()))
    return data


class AiReader:
    """A DocFile's fields as read by Claude, through the `ai_reads` cache."""

    def __init__(self, db, fetch, ask=_ask_claude):
        self.db, self.fetch, self.ask = db, fetch, ask

    def __call__(self, f: doc_status.DocFile) -> dict | None:
        if not f.ref or not f.name.lower().endswith(".pdf"):
            return None

        def produce():
            pdf = self.fetch(f.ref)
            return self.ask(pdf) if len(pdf) <= _MAX_PDF_BYTES else None
        return _cached(self.db, f.ref, f.modified, produce, self.ask is _ask_claude)


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


# -- container photos (1D) -------------------------------------------------------
# Not for HOPSON (HAI) and JMI (the user's rule).
PHOTO_SKIP = {"HAI", "JMI"}
_IMAGE = re.compile(r"(?i)\.(jpe?g|png)$")
_MAX_PHOTOS = 6

_PHOTO_PROMPT = """These photos show ONE export container at stuffing in Indonesia:
typically the container doors, the handwritten delivery note ("surat pengantar" /
"surat jalan"), the inside, and a terminal screenshot. Read (null when not shown):
- door_container_number: the container number painted on the doors, 4 letters +
  7 digits including the check digit (often boxed), e.g. "ONEU5311830".
- tare_kg: the TARE weight in kg painted on the doors, as an integer.
- sheet_container_number, sheet_seal_number, sheet_do_number: the container
  number, seal number and DO / booking number handwritten on the delivery note."""

_PHOTO_SCHEMA = {
    "type": "object",
    "properties": {
        **{k: {"type": ["string", "null"]} for k in (
            "door_container_number", "sheet_container_number", "sheet_seal_number",
            "sheet_do_number")},
        "tare_kg": {"type": ["integer", "null"]},
    },
    "required": ["door_container_number", "tare_kg", "sheet_container_number",
                 "sheet_seal_number", "sheet_do_number"],
    "additionalProperties": False,
}


def _jpeg(data: bytes) -> bytes:
    """Phone photos shrunk to what Claude reads anyway (1568 px long edge)."""
    import io

    from PIL import Image

    im = Image.open(io.BytesIO(data))
    im.thumbnail((1568, 1568))
    out = io.BytesIO()
    im.convert("RGB").save(out, "JPEG", quality=85)
    return out.getvalue()


def _ask_photos(images: list[bytes]) -> dict:
    import anthropic

    content = [{"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg",
        "data": base64.standard_b64encode(_jpeg(i)).decode()}} for i in images]
    resp = anthropic.Anthropic().beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-06-01"],
        fallbacks=[{"model": "claude-opus-4-8"}],
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": _PHOTO_SCHEMA}},
        messages=[{"role": "user", "content": content + [{"type": "text", "text": _PHOTO_PROMPT}]}],
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"AI read stopped: {resp.stop_reason}")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def _photo_sets(folder, containers) -> dict:
    """{container_no: [image nodes]} from Foto/: one subfolder per container
    ("1.ONEU5311830-galang 8 oct"), or the photos loose in Foto/ when the
    shipment has a single container."""
    from . import drive

    foto = next((c for c in drive._safe_iterdir(folder)
                 if c.is_dir() and c.name.strip().lower() == "foto"), None)
    if foto is None:
        return {}, []
    entries = drive._safe_iterdir(foto)
    out = {}
    for c in containers:
        sub = next((d for d in entries if d.is_dir() and c in _norm(d.name)), None)
        if sub is not None:
            out[c] = [f for f in drive._safe_iterdir(sub)
                      if not f.is_dir() and _IMAGE.search(f.name)]
    loose = [f for f in entries if not f.is_dir() and _IMAGE.search(f.name)]
    if len(containers) == 1 and loose and not out:
        out[containers[0]] = loose
    # Container folders whose number isn't on the VGM (a swapped container).
    strays = sorted({m.group(0) for d in entries if d.is_dir()
                     for m in [re.search(r"[A-Z]{4}\d{7}", _norm(d.name))]
                     if m and m.group(0) not in containers})
    return {c: sorted(imgs, key=lambda f: f.name)[:_MAX_PHOTOS]
            for c, imgs in out.items() if imgs}, strays


def _digits(s) -> int | None:
    d = re.sub(r"\D", "", str(s or ""))
    return int(d) if d else None


def _edits(a: str, b: str) -> int:
    """Levenshtein distance."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _handwritten_ok(written, expected) -> bool:
    """A handwritten number vs the workbook's, digits only (a seal's letters,
    "ID16903AB", are scrawled and misread), allowing two misread / dropped /
    extra digits — the handwriting is that rough.
    ponytail: also lets a genuine small slip through — tighten if needed."""
    w, e = re.sub(r"\D", "", str(written or "")), re.sub(r"\D", "", str(expected or ""))
    if len(w) < 4 or len(e) < 4:
        return True                       # nothing number-like to compare
    return e in w or w in e or _edits(w, e) <= 2


# ISO 6346 letter values: A=10 upward, skipping multiples of 11.
_ISO_LETTER = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                       [v for v in range(10, 39) if v % 11]))


def _iso6346_ok(number) -> bool:
    """A container number with a valid check digit — anything else is a misread."""
    n = _norm(number)
    if not re.fullmatch(r"[A-Z]{4}\d{7}", n):
        return False
    total = sum((int(ch) if ch.isdigit() else _ISO_LETTER[ch]) << i for i, ch in enumerate(n[:10]))
    return total % 11 % 10 == int(n[10])


def photo_check(folder, fields, db, fetch, ask=_ask_photos) -> dict | None:
    """Each VGM container's photos vs the workbook: the door number and tare,
    and the delivery note's container, seal and DO number."""
    head = {"key": "photos", "label": "Foto kontainer"}
    rows = fields.vgm_rows or {}
    if not rows:
        return None
    sets, strays = _photo_sets(folder, list(rows))
    notes = [f"folder foto {s} tidak ada di VGM" for s in strays]
    if not sets:
        return {**head, "done": False, "notes": notes + ["foto belum ada"]}
    notes += [f"{c}: foto belum ada" for c in rows if c not in sets]
    for c, imgs in sets.items():
        refs = [doc_status._ref(i) for i in imgs]
        d = _cached(db, "photos:" + ",".join(refs),
                    ",".join(doc_status._modified(i) for i in imgs),
                    lambda refs=refs: ask([fetch(r) for r in refs]), ask is _ask_photos)
        if d is None:
            return None               # undecided — keep the previous check
        vgm = rows[c]
        if (_iso6346_ok(d.get("door_container_number"))
                and not _same_number(d["door_container_number"], c)):
            notes.append(f"{c}: nomor di pintu {d['door_container_number']}")
        tare = _digits(vgm.get("tare"))
        if d.get("tare_kg") and tare and d["tare_kg"] != tare:
            notes.append(f"{c}: tare foto {d['tare_kg']} ≠ VGM {tare}")
        if not _handwritten_ok(d.get("sheet_container_number"), c):
            notes.append(f"{c}: surat pengantar menulis {d['sheet_container_number']}")
        if not _handwritten_ok(d.get("sheet_seal_number"), vgm.get("seal")):
            notes.append(f"{c}: seal surat pengantar {d['sheet_seal_number']} ≠ VGM {vgm['seal']}")
        if not _handwritten_ok(d.get("sheet_do_number"), fields.booking_number):
            notes.append(f"{c}: DO surat pengantar {d['sheet_do_number']} ≠ booking "
                         f"{fields.booking_number}")
    return {**head, "done": not notes, "notes": notes}


# -- document contents ------------------------------------------------------------
# Each document's newest file (the one its status comes from) against the SI /
# INV sheets. Claude compares, since the templates differ per exporter and the
# SI carries the team's notes about what goes on which document.
CONTENT_DOCS = {
    "peb_npe": "a PEB (Pemberitahuan Ekspor Barang, the Indonesian export declaration) or NPE",
    "fumi": "a fumigation certificate",
    "phyto": "a phytosanitary certificate",
    "coo": "a certificate of origin",
    "bl": "a bill of lading or sea waybill (draft or final)",
}
_BAG_SKIP = {"HAI", "JMI"}
_FIELD_LABEL = {
    "shipper": "Shipper", "consignee": "Consignee/buyer", "notify": "Notify party",
    "goods": "Deskripsi barang", "weight": "Berat (NW/GW)", "bags": "Jumlah bag",
    "fob": "Nilai FOB", "vessel": "Kapal/voyage", "invoice": "No./tgl invoice",
    "pod": "Port tujuan",
}

_CONTENT_PROMPT = """You check an Indonesian exporter's shipping document against the
exporter's workbook (the Shipping Instruction "SI" and invoice "Inv" sheets, given
as text: "row | column:value | ...").

The document should be {doc}. Compare, where the document shows them:
- shipper: name and details (address, contacts)
- consignee: consignee / buyer name and details
- notify: notify party name and details
- goods: description of goods{bl_goods}
- weight: net and gross weight
{bags}- fob: FOB value (only if the workbook gives an FOB value or FOB terms)
- vessel: vessel name and voyage
- invoice: invoice number and date
- pod: destination port
{extra}
Rules:
- Report only substantive differences: a wrong or missing name, address part,
  number, weight, port or vessel. Ignore case, punctuation, line breaks, spacing,
  abbreviations (PT / PT., KAB. / KABUPATEN, LTD / LIMITED), number formatting
  and rounding to 2 decimals.
- Notes in the workbook (Indonesian) are instructions: "ini jangan cantumkan di
  COO & Fumi" means that information must NOT appear on the COO and Fumi. A
  document correctly leaving it out is fine; one including it is an issue.
- Fields the document doesn't carry at all are not issues.
- If the file is not {doc} at all, set is_expected_document to false.
Write each issue's values short (the differing part, not the whole address).

Workbook:
{reference}"""

_CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_expected_document": {"type": "boolean"},
        "issues": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": list(_FIELD_LABEL)},
                "document": {"type": "string"},
                "workbook": {"type": "string"},
            },
            "required": ["field", "document", "workbook"],
            "additionalProperties": False,
        }},
    },
    "required": ["is_expected_document", "issues"],
    "additionalProperties": False,
}


def _content_prompt(code: str, exporter: str, reference: str) -> str:
    extra = {
        "coo": "- Ignore every date on the COO (the departure date is often corrected by hand).",
        "fumi": "- Ignore dates.",
    }.get(code, "")
    return _CONTENT_PROMPT.format(
        doc=CONTENT_DOCS[code], reference=reference, extra=extra,
        bl_goods=(" — on a BL it must start with the party size (e.g. \"10 X 40'HC\"), "
                  "then the goods description") if code == "bl" else "",
        bags="" if exporter.upper() in _BAG_SKIP else "- bags: number of bags\n")


def _ask_content(pdf: bytes, prompt: str) -> dict:
    import anthropic

    resp = anthropic.Anthropic().beta.messages.create(
        model=MODEL,
        max_tokens=8000,
        betas=["server-side-fallback-2026-06-01"],
        fallbacks=[{"model": "claude-opus-4-8"}],
        output_config={"effort": "medium",
                       "format": {"type": "json_schema", "schema": _CONTENT_SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf).decode()}},
            {"type": "text", "text": prompt}]}],
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"AI read stopped: {resp.stop_reason}")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def content_check(code, f, fields, exporter, db, fetch, ask=_ask_content) -> dict | None:
    """One document's newest file `f` vs the workbook. Cached per file version
    AND workbook text, so a corrected SI re-checks the document."""
    import hashlib

    if f is None or not f.ref or not f.name.lower().endswith(".pdf") or not fields.reference_text:
        return None
    prompt = _content_prompt(code, exporter, fields.reference_text)
    stamp = f"{f.modified}|{hashlib.sha1(prompt.encode()).hexdigest()[:16]}"
    d = _cached(db, f"content:{code}:{f.ref}", stamp,
                lambda: _small_enough(fetch(f.ref), lambda pdf: ask(pdf, prompt)),
                ask is _ask_content)
    if d is None:
        return None
    head = {"key": "content", "label": "Isi dokumen vs SI/INV", "file": _file(f)}
    if not d.get("is_expected_document", True):
        return {**head, "done": False, "notes": ["file ini sepertinya bukan dokumennya"]}
    notes = [f"{_FIELD_LABEL.get(i['field'], i['field'])}: dokumen {i['document']} ≠ "
             f"Excel {i['workbook']}" for i in d.get("issues") or []]
    return {**head, "done": not notes, "notes": notes}


def _small_enough(pdf: bytes, then):
    return then(pdf) if len(pdf) <= _MAX_PDF_BYTES else None


def content_checks(db, folder, fields, exporter: str, shipment: dict,
                   ask=_ask_content, codes=None) -> None:
    """The content check onto each document item (PEB, Fumi, Phyto, COO, BL),
    only while it is at Draft diterima: the highlights are for annotating the
    draft and asking for a revision. Once the item moves on (revision asked,
    OK, final) the check is cleared. Undecidable -> left as is."""
    from .action_items import ActionItems

    files = doc_status.list_files(folder)
    if not files:
        return
    fetch = doc_status._fetcher(folder)
    items = ActionItems(db)
    seqs = doc_status.own_sequences(shipment, files)
    reader = doc_status.DraftReader(db, fetch)
    status = {r["code"]: (r["status"], r["checks"]) for r in db.query(
        "SELECT code, status, checks FROM action_items WHERE shipment_id=?", (shipment["id"],))}
    for code in CONTENT_DOCS:
        if code not in status or (codes is not None and code not in codes):
            continue
        current, old = status[code]
        inferred = doc_status.infer(code, files, exporter, seqs, reader)
        # The draft must be the newest evidence too: a status still at Draft
        # diterima by hand while a final has arrived isn't a draft to annotate.
        # A status below Draft diterima is about to move up to it (the status
        # sync runs after this in the same scan), so check it already.
        at_draft = current == "draft_received" or (
            doc_status.RANK.get(current, 0) < doc_status.RANK["draft_received"])
        if not at_draft or not inferred or inferred.status != "draft_received":
            if old and old != "[]":
                items.set_checks(shipment["id"], code, [])
            continue
        check = content_check(code, inferred.file, fields, exporter, db, fetch, ask)
        if check is not None:
            items.set_checks(shipment["id"], code, [check])


def compute(db, folder, fields, previous: dict, exporter: str = "") -> list[dict]:
    """[do, atd] (not AMI/PMA/CKJ) and [photos] (not HAI/JMI) for a shipment
    folder; an undecidable check keeps `previous[key]`."""
    from .si_checks import SKIP_EXPORTERS

    ex = exporter.upper()
    files = doc_status.list_files(folder)
    keys = ([] if ex in SKIP_EXPORTERS else ["do", "atd"]) + ([] if ex in PHOTO_SKIP else ["photos"])
    if not files:
        return [previous[k] for k in keys if previous.get(k)]
    fetch = doc_status._fetcher(folder)
    read = AiReader(db, fetch)
    run = {"do": lambda: do_check(files, fields, read),
           "atd": lambda: atd_check(files, fields, read),
           "photos": lambda: photo_check(folder, fields, db, fetch)}
    out = []
    for key, check in ((k, run[k]()) for k in keys):
        check = check if check is not None else previous.get(key)
        if check:
            out.append(check)
    return out
