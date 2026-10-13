"""What is inside a draft PDF (doc automation Phase 1B) — deterministic, no AI.

* Title: the first page's text says what the document is, whatever the file is
  called — a fumigation certificate ("FUMIGATION CERTIFICATE", "TREATMENT
  CERTIFICATE – METHYL BROMIDE FUMIGATION") or a bill of lading ("BILL OF
  LADING"), even when named "0742CN.pdf" or "2334357090-20260911041948.PDF".
* Review marks: the team marks drafts up in a PDF editor, so the marks are PDF
  annotations — typewriter text ("OK", "OK ORIGINAL", corrected values, "====="
  strike-through, notes) and arrows — each with its author, time and colour.
  Only red marks count. The newest red mark decides: "OK" -> Draft OK, anything
  else -> Draf direvisi (a mark after an OK is another revision). No red marks ->
  nothing (the draft is just received).
* COO exception: its draft carries the *estimated* departure date, so the red
  mark updating it to the actual date (from the BL) is expected, not a revision.
  Typed marks on the departure-date row, and the arrows that point at them, are
  ignored for COO; any other red mark (vessel, weights, ...) still counts.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

FUMI_TITLE = re.compile(r"(?i)fumigation|methyl\s+bromide|treatment\s+certificate")
BL_TITLE = re.compile(r"(?i)bill\s+of\s+lading")
_OK_RE = re.compile(r"(?i)^\s*ok\b")
_RGB = re.compile(rb"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(rg|RG)")
# A printed date on the row a mark sits on: "SEPTEMBER 13, 2026", "13 SEP 2026",
# "13/09/2026".
_ROW_DATE = re.compile(
    r"(?i)\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|\b\d{1,2}[\s/.-](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{1,2})[a-z]*"
    r"[\s/.-]\d{2,4}\b")
_DATE_TOKEN = re.compile(r"(?i)^(\d{1,2},?|(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?|20\d\d)$")


def _on_date(box, words) -> bool:
    """Is a typed mark the date update? It *sits on* a printed line when it covers
    at least half that line's height — then that line decides (the "----" struck
    over "SEPTEMBER 13, 2026" does; "==== MAO GANG..." on the vessel line doesn't).
    Otherwise it floats in a gap — the new day ("14") is typed just above the old
    one — and counts when the printed word right under/over it (within 12pt, same
    column) is part of a date."""
    x0, x1, top, bottom = box
    column = [w for w in words if w["x1"] > x0 - 2 and w["x0"] < x1 + 2]

    def row(w):
        return " ".join(v["text"] for v in words if abs(v["top"] - w["top"]) <= 2)

    def overlap(w):
        return min(bottom, w["bottom"]) - max(top, w["top"])

    sits = [w for w in column if overlap(w) >= 0.5 * (w["bottom"] - w["top"])]
    if sits:
        return any(_ROW_DATE.search(row(w)) for w in sits)
    near = [w for w in column if max(w["top"] - bottom, top - w["bottom"], 0) <= 12]
    return any(_DATE_TOKEN.match(w["text"]) and _ROW_DATE.search(row(w)) for w in near)


_PDF_DATE = re.compile(r"D:(\d{4})(\d\d)(\d\d)(\d\d)?(\d\d)?(\d\d)?([+\-Z])?(\d\d)?'?(\d\d)?")
# Annotation kinds that are marks (Link / Popup / Widget are not).
_MARK_KINDS = {"FreeText", "Line", "Ink", "Square", "Circle", "Polygon", "PolyLine",
               "StrikeOut", "Underline", "Squiggly", "Highlight", "Caret", "Text"}


@dataclass(frozen=True)
class Mark:
    kind: str            # "ok" or "correction"
    author: str
    when: str            # UTC 'YYYY-MM-DDTHH:MM:SS' ('' if the PDF has no date)
    text: str = ""
    on_date: bool = False    # typed on a printed-date row (COO's departure date)


@dataclass(frozen=True)
class Content:
    title_kind: str | None       # "fumi" / "bl" / None
    marks: tuple                 # red marks only


def _name(value) -> str:
    """A PDF name/string as text ("/'FreeText'" -> "FreeText")."""
    if isinstance(value, bytes):
        value = value.decode("latin1", "ignore")
    s = str(getattr(value, "name", value) or "")
    return s.strip("/'\" ")


def _text(value) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-16") if value[:2] in (b"\xfe\xff", b"\xff\xfe") \
                else value.decode("latin1")
        except UnicodeDecodeError:
            return value.decode("latin1", "ignore")
    return str(value or "")


def pdf_date(value) -> str:
    """'D:20260929103501+07'00'' -> '2026-09-29T03:35:01' (UTC)."""
    m = _PDF_DATE.match(_text(value))
    if not m:
        return ""
    y, mo, d, h, mi, s, sign, oh, om = m.groups()
    stamp = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
    if sign in ("+", "-") and oh:
        offset = timedelta(hours=int(oh), minutes=int(om or 0))
        stamp = stamp - offset if sign == "+" else stamp + offset
    return stamp.isoformat()


def _is_red(rgb) -> bool:
    try:
        r, g, b = (float(x) for x in rgb[:3])
    except (TypeError, ValueError):
        return False
    return r >= 0.8 and g <= 0.4 and b <= 0.4


def _appearance(data) -> bytes:
    try:
        ap = data["AP"]["N"]
        ap = ap.resolve() if hasattr(ap, "resolve") else ap
        return ap.get_data() or b""
    except Exception:      # noqa: BLE001 - no/odd appearance stream
        return b""


def _colour(data, kind: str):
    """The mark's visible colour. Lines/shapes: their C array. Typed text: the fill
    colour in DA ("... 1 0.24 0.24 rg") or, failing that, in the drawn appearance
    (some editors only record it there)."""
    if kind != "FreeText" and data.get("C"):
        return data.get("C")
    for source in (_text(data.get("DA")).encode("latin1", "ignore"), _appearance(data)):
        fills = [m for m in _RGB.finditer(source)
                 if m.group(4) == (b"RG" if kind != "FreeText" else b"rg")]
        if fills:
            return fills[-1].groups()[:3]
    return data.get("C")


def read_pdf(data: bytes) -> Content | None:
    """Title kind + red review marks, or None if the PDF can't be read."""
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            first = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
            raw = []
            for page in pdf.pages:
                words = None
                for a in page.annots or []:
                    if words is None:
                        words = page.extract_words()
                    raw.append((a.get("data", {}),
                                _on_date((a["x0"], a["x1"], a["top"], a["bottom"]), words)))
    except Exception:      # noqa: BLE001 - a corrupt/truncated PDF is just unreadable
        return None
    title = "fumi" if FUMI_TITLE.search(first) else "bl" if BL_TITLE.search(first) else None
    marks = []
    for d, on_date in raw:
        kind = _name(d.get("Subtype"))
        if kind not in _MARK_KINDS or not _is_red(_colour(d, kind) or ()):
            continue
        text = _text(d.get("Contents")).strip()
        if kind in ("FreeText", "Text") and not text:
            continue                        # an empty text box marks nothing
        marks.append(Mark("ok" if _OK_RE.match(text) else "correction",
                          _text(d.get("T")).strip(),
                          pdf_date(d.get("M") or d.get("CreationDate")), text, on_date))
    return Content(title, tuple(marks))


def review(marks, code: str | None = None) -> Mark | None:
    """The deciding mark: the newest red one (its kind gives Draft OK / revisi).
    For COO, the expected departure-date update is left out: typed marks on the
    date row, and bare arrows/lines (they only point a typed fix at its spot)."""
    if code == "coo":
        marks = [m for m in marks if m.kind == "ok" or (m.text and not m.on_date)]
    return max(marks, key=lambda m: m.when, default=None)


def marks_to_json(marks) -> str:
    import json
    return json.dumps([m.__dict__ for m in marks])


def marks_from_json(raw: str | None) -> tuple:
    import json
    return tuple(Mark(**m) for m in json.loads(raw or "[]"))
