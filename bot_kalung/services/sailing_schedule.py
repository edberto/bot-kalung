"""Carrier sailing schedule — the Tresnamuda / Jameson Freight route PDF.

Route 48 (BELAWAN -> PORT KLANG WEST) lists every MTT REYA voyage with its
Belawan closing time, ETA and ETD weeks before BNCT shows the voyage. Those
dates are stored in `vessel_schedules` as reference only: nothing here touches
`monitored_vessels`, so a voyage's status (scheduled / berthed / ...) is still
set by BNCT alone.
"""

from __future__ import annotations

import io
import re
from datetime import datetime

SCHEDULE_URL = "https://www.tresnamuda.co.id/service-schedule/download-route-file/48"
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


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


def refresh(db) -> int:
    """Download, parse and store the schedule; returns the voyage count.

    The PDF lives only in memory (parsed from BytesIO) and is never written to
    disk on the worker — only the extracted dates are kept, in the database."""
    import requests

    # ponytail: re-downloaded every scan (~640 KB / 30 min); cache by
    # Last-Modified if the carrier ever complains.
    resp = requests.get(SCHEDULE_URL, timeout=30,
                        headers={"User-Agent": "Mozilla/5.0 bot-kalung"})
    resp.raise_for_status()
    rows = parse_pdf(resp.content)
    store(db, rows, datetime.now().isoformat(timespec="seconds"))
    return len(rows)
