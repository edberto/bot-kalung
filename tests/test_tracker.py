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
from bot_kalung.services.shipments import Shipments

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


def mark_done(folder: Path) -> None:
    """Put an export document in the send folder so the shipment reads as done."""
    dok = folder / "Dok kirim"
    dok.mkdir(exist_ok=True)
    (dok / "NIT-Karachi-INV.pdf").write_text("x", encoding="utf-8")


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
    mark_done(amj1)                              # AMJ1 has its export docs -> done
    make_shipment(root / "NMEHMOOD & CV.Hassan" / "2026", "1.k", "NIT01")

    db = Database(db_path_for(root))
    db.initialize()
    shipments = Shipments(db)

    result = tracker.run_scan(db, root, year=2026, read_fields=fake_fields)

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

    # ...they seed action items instead (fake_fields -> Pakistan, so the
    # quarantine trio is present).
    item_codes = {r["code"] for r in db.query(
        "SELECT code FROM action_items WHERE shipment_id=?", (amj2["id"],))}
    check("import seeds action items",
          {"si_vgm", "fumi", "phyto", "coo", "bl", "dok_kirim"} <= item_codes)

    # Registry holds every handled (code, seq): imported + done.
    keys = tracker.ScannedRegistry(db).registered_keys()
    check("registry records imported and done alike",
          {("AMJ", 1), ("AMJ", 2), ("AMJ", 3), ("NIT", 1)} <= keys)

    # ---- vessel auto-link (Phase 3) --------------------------------------
    from bot_kalung.services.vessel_monitor import MonitoredVessels
    integra = [r for r in MonitoredVessels(db).all()
               if r["vessel_name"] == "INTEGRA"]
    voyages = {r["voyage"] for r in integra}
    check("import auto-adds the shipment's vessel to Monitor Kapal",
          "162E" in voyages)
    check("auto-add fills the 3-voyage window",
          {"162E", "163E", "164E"} <= voyages)
    check("the vessel voyage is added once, not per shipment",
          sum(1 for r in integra if r["voyage"] == "162E") == 1)

    # display-time join: every active shipment on this voyage is matched.
    labels = {f"{m['exporter_code']}{m['sequence_number']}"
              for m in shipments.for_voyage("INTEGRA", "162E")}
    check("for_voyage joins active shipments to a voyage",
          {"AMJ2", "AMJ3", "NIT1"} <= labels)

    # containers populated from the VGM read (Phase 4)
    from bot_kalung.services.containers import Containers
    conts = Containers(db).for_shipment(amj2["id"])
    check("import populates the shipment's containers",
          any(c.container_no == "TRHU5986693" for c in conts))

    # ---- delta: a second scan imports nothing new -------------------------
    again = tracker.run_scan(db, root, year=2026, read_fields=fake_fields)
    check("a rescan imports nothing new", again.imported == [])
    check("a rescan does not duplicate rows",
          len(db.query("SELECT id FROM shipments")) == 3)

    # ---- a new in-sequence folder appears ---------------------------------
    make_shipment(amj, "4.5x40-karachi", "AMJ04")
    third = tracker.run_scan(db, root, year=2026, read_fields=fake_fields)
    check("a newly-added in-sequence folder is imported", third.imported == ["AMJ4"])

    # ---- a renamed folder must NOT duplicate the shipment ------------------
    # Rename AMJ2's folder and drop its registry row (simulating drift). The scan
    # re-discovers (AMJ,2); it must skip it — never create a duplicate and never
    # modify the existing shipment row.
    before = len(db.query("SELECT id FROM shipments"))
    (amj / "2.5x40-karachi").rename(amj / "2.5x40-karachi-Buyer-Renamed")
    db.execute("DELETE FROM scanned_shipments WHERE exporter_code='AMJ' "
               "AND sequence_number=2")
    rescan = tracker.run_scan(db, root, year=2026, read_fields=fake_fields)
    check("a renamed folder does not create a duplicate",
          rescan.imported == []
          and len(db.query("SELECT id FROM shipments")) == before)
    check("the existing shipment row is left untouched",
          db.query_one("SELECT folder_path FROM shipments WHERE exporter_code='AMJ'"
                       " AND sequence_number=2")["folder_path"]
          .endswith("2.5x40-karachi"))
    check("the re-discovered key is re-registered so later scans skip it",
          ("AMJ", 2) in tracker.ScannedRegistry(db).registered_keys())

    # ---- vessel/voyage change detection -----------------------------------
    # NIT1 was imported on INTEGRA 162E. Its workbook now reports 163E; a rescan
    # must move it, re-point the Monitor Kapal link, and report the change.
    nit1 = db.query_one("SELECT * FROM shipments WHERE exporter_code='NIT' "
                        "AND sequence_number=1")

    def moved_fields(folder):
        f = fake_fields(folder)
        f.voyage = "163E"                       # same vessel, new voyage
        return f

    changed = tracker.run_scan(db, root, year=2026, read_fields=fake_fields,
                               reread_fields=moved_fields)
    nit1_after = db.query_one("SELECT * FROM shipments WHERE id=?", (nit1["id"],))
    check("a moved voyage is detected and updated on the row",
          nit1_after["voyage"] == "163E" and nit1_after["vessel_name"] == "INTEGRA")
    check("the change is listed in the scan report",
          any("NIT1" in c and "163E" in c for c in changed.vessel_changes))
    check("the new voyage is auto-monitored",
          any(r["vessel_name"] == "INTEGRA" and r["voyage"] == "163E"
              for r in MonitoredVessels(db).all()))

    steady = tracker.run_scan(db, root, year=2026, read_fields=fake_fields,
                              reread_fields=moved_fields)
    check("no change is reported once the row already matches",
          steady.vessel_changes == [])

    def blank_fields(folder):
        return excel.ShipmentFields()           # unreadable (e.g. .xls): no vessel

    tracker.run_scan(db, root, year=2026, read_fields=fake_fields,
                     reread_fields=blank_fields)
    nit1_blank = db.query_one("SELECT * FROM shipments WHERE id=?", (nit1["id"],))
    check("an unreadable workbook never blanks the stored vessel/voyage",
          nit1_blank["vessel_name"] == "INTEGRA" and nit1_blank["voyage"] == "163E")


print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Tracker OK - all checks passed.")
