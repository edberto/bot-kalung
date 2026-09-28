"""Carrier sailing-schedule parsing + storage (no network: table rows as
pdfplumber extracts them from the live Tresnamuda route-48 PDF)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.db import Database, db_path_for
from bot_kalung.services import sailing_schedule

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


TABLE = [
    ['ROUTE : BELAWAN - PORT KLANG WEST', None, None, None, None, None, None],
    ['VESSEL', 'VOY', 'BELAWAN', None, None, None, 'PORT KLANG WEST'],
    [None, None, 'Closing Time', None, 'ETA', 'ETD', 'ETA'],
    ['MTT REYA', '26RY142N', '27-Sep-2026', '08:00 Hrs', '27-Sep-2026', '28-Sep-2026', '29-Sep-2026'],
    ['MTT REYA', '26RY143N', '30-Sep-2026', '08:00 Hrs', '30-Sep-2026', '1-Oct-2026', '2-Oct-2026'],
]

rows = sailing_schedule.parse_rows(TABLE)
check("header rows are skipped, voyage rows kept", len(rows) == 2)
check("closing date + time parsed",
      rows[0]["closing_at"] == "2026-09-27T08:00")
check("ETA / ETD Belawan parsed (not the POD ETA)",
      rows[1]["eta_belawan"] == "2026-09-30" and rows[1]["etd_belawan"] == "2026-10-01")
check("vessel + voyage kept as listed",
      rows[0]["vessel_name"] == "MTT REYA" and rows[0]["voyage"] == "26RY142N")

with tempfile.TemporaryDirectory() as tmp:
    db = Database(db_path_for(Path(tmp)))
    db.initialize()
    sailing_schedule.store(db, rows, "2026-09-28T10:00:00")
    rows[0]["etd_belawan"] = "2026-09-29"                  # carrier revises a date
    sailing_schedule.store(db, rows, "2026-09-28T10:30:00")
    stored = db.query("SELECT * FROM vessel_schedules ORDER BY voyage")
    check("a re-store upserts instead of duplicating", len(stored) == 2)
    check("a revised date overwrites the old one",
          stored[0]["etd_belawan"] == "2026-09-29")
    check("the schedule never creates a monitored voyage (status untouched)",
          db.query("SELECT * FROM monitored_vessels") == [])

shifted = sailing_schedule.parse_rows([   # route-27 PDF: extra empty first column
    ['', 'ROUTE : BELAWAN - SINGAPORE', None, None, None, None, '24-Sep-26', None],
    ['', 'MAO GANG GUANG ZHOU', '023N', '2-Sep-2026', '10:00 Hrs', '2-Sep-2026', '4-Sep-2026', '7-Sep-2026'],
    [None, 'MAO GANG GUANG ZHOU', '024N', '9-Sep-2026', '10:00 Hrs', '9-Sep-2026', '11-Sep-2026', '14-Sep-2026'],
])
check("a PDF with an empty leading column still parses",
      len(shifted) == 2 and shifted[0]["vessel_name"] == "MAO GANG GUANG ZHOU"
      and shifted[0]["closing_at"] == "2026-09-02T10:00"
      and shifted[1]["etd_belawan"] == "2026-09-11")

# ---- Evergreen ShipmentLink HTML (MM/DD, no year; colspan header) ----------
from datetime import date

EVERGREEN = """<table><tr><td align='center'><table width='100%'>
<tr><td class='f09tilb1' colspan='2'> &nbsp; </td><td class='f09tilb1'>BELAWAN</td>
<td class='f09tilb1'>PORT KLANG WEST PORT</td><td class='f09tilb1'>LAEM CHABANG</td></tr>
<tr><td nowrap>GREEN CELESTE 0808-132N</td><td nowrap>ARR<BR>DEP</td>
<td nowrap>10/02<BR>10/03</td><td nowrap>10/03<BR>10/04</td><td nowrap>10/08<BR>10/09</td></tr>
<tr><td nowrap>GREEN CELESTE 0823-139N</td><td nowrap>ARR<BR>DEP</td>
<td nowrap>01/05<BR>01/06</td><td nowrap>01/07<BR>01/08</td><td nowrap>---<BR>---</td></tr>
</table></td></tr></table>"""

ev = sailing_schedule.parse_evergreen_html(EVERGREEN, date(2026, 9, 28))
check("Evergreen rows parsed (outer wrapper table ignored)", len(ev) == 2)
check("the BELAWAN column is found despite the colspan header",
      ev[0]["eta_belawan"] == "2026-10-02" and ev[0]["etd_belawan"] == "2026-10-03")
check("vessel and full Evergreen voyage split correctly",
      ev[0]["vessel_name"] == "GREEN CELESTE" and ev[0]["voyage"] == "0808-132N")
check("a month/day before today rolls into next year",
      ev[1]["eta_belawan"] == "2027-01-05")
check("Evergreen lists no closing time", ev[0]["closing_at"] is None)
check("'---' is no date", sailing_schedule._mmdd("---", date(2026, 9, 28)) is None)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Sailing schedule OK - all checks passed.")
