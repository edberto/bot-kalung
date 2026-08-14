"""The scan coordinator: imports shipments, populates fields, records the
registry, and rescans as a delta. Excel and PDF reads are injected, so this runs
with no COM and no real files beyond the folder tree.
"""

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.db import Database, db_path_for
from bot_kalung.services import excel, tracker

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def make_shipment(parent: Path, folder_name: str, code_seq: str):
    folder = parent / folder_name
    folder.mkdir(parents=True)
    (folder / f"{code_seq}-Karachi-VGM,SI,Inv,PL.xlsx").write_text("x", encoding="utf-8")
    return folder


def add_bl_scan(folder: Path) -> Path:
    dok = folder / "Dok kirim"
    dok.mkdir(exist_ok=True)
    pdf = dok / "SCAN BL.pdf"
    pdf.write_text("x", encoding="utf-8")
    return pdf


def fake_fields(folder):
    """Stand-in for the Excel read — the same shape read_shipment_fields returns."""
    f = excel.ShipmentFields()
    f.destination_port = "Karachi"
    f.destination_country = "Pakistan"
    f.etd = date(2026, 1, 27)
    f.vessel_name = "INTEGRA"
    f.voyage = "162E"
    f.booking_number = "2318229000"
    f.container_quantity = 5
    f.container_size_short = "40'HC"
    f.containers = ["TRHU5986693"]
    return f


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"

    amj = root / "AMJ" / "2026"
    amj1 = make_shipment(amj, "1.5x40-karachi", "AMJ01")
    make_shipment(amj, "2.5x40-karachi", "AMJ02")
    make_shipment(amj, "3.5x40-karachi", "AMJ03")
    bl = add_bl_scan(amj1)                       # AMJ1 already has its BL -> done
    make_shipment(root / "NMEHMOOD & CV.Hassan" / "2026", "1.k", "NIT01")

    reader = lambda p: "BILL OF LADING" if str(p) == str(bl) else "PL"

    db = Database(db_path_for(root))
    db.initialize()

    result = tracker.run_scan(db, root, year=2026, read_fields=fake_fields,
                              page1_text=reader)

    imported = set(result.imported)
    check("imports the in-run, not-done shipments",
          {"AMJ2", "AMJ3", "NIT1"} <= imported)
    check("does not import the done shipment", "AMJ1" not in imported)
    check("records the done shipment as completed", "AMJ1" in result.completed)

    active = db.query("SELECT * FROM shipments ORDER BY exporter_code, sequence_number")
    labels = {f"{r['exporter_code']}{r['sequence_number']}" for r in active}
    check("three shipments exist in the database", labels == {"AMJ2", "AMJ3", "NIT1"})

    amj2 = db.query_one(
        "SELECT * FROM shipments WHERE exporter_code='AMJ' AND sequence_number=2")
    check("destination was read onto the row", amj2["destination_port"] == "Karachi")
    check("ETD was read onto the row", amj2["etd_belawan"] == "2026-01-27")
    check("vessel + voyage were read onto the row",
          amj2["vessel_name"] == "INTEGRA" and amj2["voyage"] == "162E")
    check("container party size was read", amj2["container_quantity"] == 5)
    check("Pakistan flags quarantine required", amj2["quarantine_required"] == 1)
    check("folder_path points at the scanned folder",
          amj2["folder_path"].endswith("2.5x40-karachi"))

    # Scanned shipments do not seed the old A1..E6 workflow steps.
    steps = db.query("SELECT * FROM workflow_steps WHERE shipment_id=?",
                     (amj2["id"],))
    check("no legacy workflow steps are seeded", len(steps) == 0)

    # Registry holds every handled (code, seq): imported + done.
    keys = tracker.ScannedRegistry(db).registered_keys()
    check("registry records imported and done alike",
          {("AMJ", 1), ("AMJ", 2), ("AMJ", 3), ("NIT", 1)} <= keys)

    # ---- delta: a second scan imports nothing new -------------------------
    again = tracker.run_scan(db, root, year=2026, read_fields=fake_fields,
                             page1_text=reader)
    check("a rescan imports nothing new", again.imported == [])
    check("a rescan does not duplicate rows",
          len(db.query("SELECT id FROM shipments")) == 3)

    # ---- a new in-sequence folder appears ---------------------------------
    make_shipment(amj, "4.5x40-karachi", "AMJ04")
    third = tracker.run_scan(db, root, year=2026, read_fields=fake_fields,
                             page1_text=reader)
    check("a newly-added in-sequence folder is imported", third.imported == ["AMJ4"])


print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Tracker OK - all checks passed.")
