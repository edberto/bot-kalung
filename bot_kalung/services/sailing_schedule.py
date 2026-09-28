"""Carrier sailing schedules, stored as Belawan reference dates.

* Tresnamuda / Jameson Freight route PDFs — MTT REYA (route 48) and
  MAO GANG GUANG ZHOU (route 27): closing, ETA, ETD.
* Evergreen ShipmentLink TMI page (HTML) — GREEN CELESTE / EVER CONCERT: ETA,
  ETD (no closing time; dates are MM/DD with no year).

Both list voyages weeks before BNCT does. Dates go into `vessel_schedules` as
reference only: nothing here touches `monitored_vessels`, so a voyage's status
(scheduled / berthed / ...) is still set by BNCT alone.
"""

from __future__ import annotations

import html
import io
import logging
import re
from datetime import date, datetime

from .excel import _split_vessel_voyage

log = logging.getLogger(__name__)

# Tresnamuda route PDFs: 48 = Belawan-Port Klang West (MTT REYA),
# 27 = Belawan-Singapore (MAO GANG GUANG ZHOU).
TRESNAMUDA_URLS = [
    "https://www.tresnamuda.co.id/service-schedule/download-route-file/48",
    "https://www.tresnamuda.co.id/service-schedule/download-route-file/27",
]
EVERGREEN_URL = "https://ss.shipmentlink.com/tvs2/download_txt/TMI_9.html"
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
# A <tr> holding no nested <tr> (the page wraps its tables in an outer table).
_ROW_RE = re.compile(r"(?is)<tr[^>]*>((?:(?!<tr).)*?)</tr>")
_CELL_RE = re.compile(r"(?is)<td([^>]*)>(.*?)</td>")


def _iso_date(text: str) -> str | None:
    try:
        return datetime.strptime((text or "").strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def parse_rows(table_rows) -> list[dict]:
    """Voyage rows from the PDF's table. Layout per row:
    [vessel, voyage, closing date, closing time, ETA Belawan, ETD Belawan, ETA POD].
    Header/footer rows fail the date parse and are skipped."""
    out = []
    for row in table_rows:
        cells = [(c or "").strip() for c in row]
        while cells and not cells[0]:      # route-27 PDF has an empty leading column
            cells.pop(0)
        if len(cells) < 6 or not cells[0] or not cells[1]:
            continue
        closing = _iso_date(cells[2])
        if closing is None:
            continue
        t = _TIME_RE.search(cells[3])
        out.append({
            "vessel_name": cells[0].upper(),
            "voyage": cells[1].upper(),
            "closing_at": f"{closing}T{int(t.group(1)):02d}:{t.group(2)}" if t else closing,
            "eta_belawan": _iso_date(cells[4]),
            "etd_belawan": _iso_date(cells[5]),
        })
    return out


def parse_pdf(data: bytes) -> list[dict]:
    import pdfplumber

    rows = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                rows += parse_rows(table)
    return rows


def _mmdd(text: str, today: date) -> str | None:
    """An Evergreen "MM/DD" with no year: a month/day earlier than today's is
    next year (the schedule only runs forward), otherwise this year."""
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})\s*", text or "")
    if not m:
        return None                                  # e.g. "---"
    month, day = int(m.group(1)), int(m.group(2))
    year = today.year + (1 if (month, day) < (today.month, today.day) else 0)
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_evergreen_html(text: str, today: date) -> list[dict]:
    """Voyage rows from the ShipmentLink schedule page. Each table's header
    names the ports; the BELAWAN column holds "ARR<br>DEP" dates. The header's
    first cell spans two columns, so cells are expanded by colspan to align."""
    out, belawan = [], None
    for body in _ROW_RE.findall(text):
        cells = []
        for attrs, raw in _CELL_RE.findall(body):
            span = re.search(r"colspan\s*=\s*['\"]?(\d+)", attrs)
            value = html.unescape(re.sub(r"<[^>]+>", "",
                                         re.sub(r"(?i)<br\s*/?>", "\n", raw))).strip()
            cells += [value] * (int(span.group(1)) if span else 1)
        upper = [c.upper() for c in cells]
        if "BELAWAN" in upper:                       # a header row
            belawan = upper.index("BELAWAN")
            continue
        if belawan is None or len(cells) <= belawan:
            continue
        vessel, voyage = _split_vessel_voyage(cells[0])
        if not voyage:
            continue
        arr_dep = cells[belawan].split("\n") + [""]
        eta, etd = _mmdd(arr_dep[0], today), _mmdd(arr_dep[1], today)
        if eta or etd:
            out.append({"vessel_name": vessel.upper(), "voyage": voyage,
                        "closing_at": None, "eta_belawan": eta, "etd_belawan": etd})
    return out


def store(db, rows: list[dict], now: str) -> None:
    """Upsert by (vessel, voyage). This table isn't realtime-subscribed, so
    rewriting unchanged rows each scan costs nothing in the PWA."""
    with db.cursor(write=True) as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO vessel_schedules (vessel_name, voyage, closing_at, "
                "eta_belawan, etd_belawan, updated_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT (vessel_name, voyage) DO UPDATE SET "
                "closing_at=excluded.closing_at, eta_belawan=excluded.eta_belawan, "
                "etd_belawan=excluded.etd_belawan, updated_at=excluded.updated_at",
                (r["vessel_name"], r["voyage"], r["closing_at"],
                 r["eta_belawan"], r["etd_belawan"], now))


def _get(url: str):
    import requests

    resp = requests.get(url, timeout=30,
                        headers={"User-Agent": "Mozilla/5.0 bot-kalung"})
    resp.raise_for_status()
    return resp


def refresh(db) -> int:
    """Download, parse and store every carrier's schedule; returns the voyage
    count. One carrier failing doesn't block the other.

    The PDF lives only in memory (parsed from BytesIO) and is never written to
    disk on the worker — only the extracted dates are kept, in the database."""
    now = datetime.now()
    sources = [(f"tresnamuda {url.rsplit('/', 1)[-1]}", url, lambda r: parse_pdf(r.content))
               for url in TRESNAMUDA_URLS]
    sources.append(("evergreen", EVERGREEN_URL,
                    lambda r: parse_evergreen_html(r.text, now.date())))
    rows = []
    # ponytail: re-downloaded every scan (two ~650 KB PDFs + 15 KB HTML / 30 min);
    # cache by Last-Modified if a carrier ever complains.
    for name, url, parse in sources:
        try:
            rows += parse(_get(url))
        except Exception:      # noqa: BLE001 - one carrier down must not block the other
            log.exception("schedule: %s failed", name)
    store(db, rows, now.isoformat(timespec="seconds"))
    return len(rows)
