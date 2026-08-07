"""Standalone vessel monitoring (2026-08-06).

The MonitoredVessels store and the VesselMonitor transitions, with no shipment
behind them. Readings are built directly (no network), mirroring test_bnct.
"""

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.context import AppContext
from bot_kalung.services.bnct import BnctReading, BnctVessel
from bot_kalung.services.notifications import NotificationStore
from bot_kalung.services.vessel_monitor import (
    MonitoredVessels, VesselMonitor, summarise,
)
from bot_kalung.ui.bnct_display import describe_record

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


NOW = datetime(2026, 8, 6, 9, 0, 0).isoformat()


def schedule_reading():
    return BnctReading(True, "schedule", NOW, BnctVessel(
        "ptp", "schedule", "MV. EVER CONCERT", "0800-088S", "0800-088N",
        etd="03/08/2026 12:00", open_billing="01/08 07:00",
        open_stacking="01/08 09:00", clossing="02/08 08:00",
        clossing_reefer="02/08 10:00"))


def alongside_reading(remain=550):
    return BnctReading(True, "alongside", NOW, BnctVessel(
        "tpkb", "alongside", "MV. EVER CONCERT", "0800-088S", "0800-088N",
        loading_plan=800, loading_actual=800 - remain, loading_remain=remain,
        discharge_plan=700, discharge_remain=20))


def notfound_reading():
    return BnctReading(False, None, NOW, None, note="belum terjadwal")


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)
    ctx = AppContext()
    ctx.create(root)
    store = MonitoredVessels(ctx.db)
    monitor = VesselMonitor(ctx.db)
    notifications = NotificationStore(ctx.db)

    # ---- store CRUD --------------------------------------------------------
    vid = store.add("EVER CONCERT", "0800-088N")
    check("vessel added and listed", len(store.all()) == 1)
    check("get returns the row", store.get(vid)["vessel_name"] == "EVER CONCERT")
    check("monitored() is everything still watched", len(store.monitored()) == 1)

    # ---- transitions (same as shipment monitoring) -------------------------
    notes = monitor.process(vid, schedule_reading())
    check("first schedule sighting notifies once",
          len(notes) == 1 and notes[0].kind == "schedule")
    check("a vessel notification carries no shipment_id",
          notes[0].shipment_id is None)
    check("the label is the vessel name + voyage",
          notes[0].title.startswith("EVER CONCERT 0800-088N"))
    check("last state persisted on the row",
          store.get(vid)["last_found"] == 1
          and store.get(vid)["last_phase"] == "schedule")

    sched_rec = json.loads(store.get(vid)["last_reading"])
    check("the schedule reading is stored with Clossing",
          sched_rec["clossing"] == "02/08 08:00"
          and sched_rec["clossing_reefer"] == "02/08 10:00")
    check("the card shows Clossing for a scheduled vessel",
          "Clossing" in describe_record(sched_rec)[2])

    check("no repeat while still scheduled",
          monitor.process(vid, schedule_reading()) == [])

    notes = monitor.process(vid, alongside_reading())
    check("moving alongside notifies", any(n.kind == "alongside" for n in notes))
    check("the alongside summary is stored for the card",
          "sandar" in (store.get(vid)["last_summary"] or "").lower())

    along_rec = json.loads(store.get(vid)["last_reading"])
    check("Clossing is carried forward once the vessel berths",
          along_rec["clossing"] == "02/08 08:00")
    check("alongside adds discharge info", along_rec["discharge_remain"] == 20)
    detail = describe_record(along_rec)[2]
    check("the alongside card shows Discharge and the carried-forward Clossing",
          "Discharge" in detail and "Clossing" in detail)

    notes = monitor.process(vid, alongside_reading(remain=2))
    check("crossing the threshold fires the departing alert",
          any(n.kind == "departing" for n in notes))
    check("departing tells them to pay LOLO",
          any("LOLO" in n.body for n in notes if n.kind == "departing"))
    check("departing does not repeat",
          monitor.process(vid, alongside_reading(remain=2)) == [])

    # ---- notifications persisted with a NULL shipment ----------------------
    null_rows = ctx.db.query(
        "SELECT * FROM notifications WHERE shipment_id IS NULL")
    check("vessel notifications persist with no shipment_id", len(null_rows) >= 3)
    check("they drive the unread counter", notifications.unread_count() >= 3)

    check("a not-found reading summarises clearly",
          "Belum ditemukan" in summarise(notfound_reading()))

    # ---- removal -----------------------------------------------------------
    store.delete(vid)
    check("vessel removed from the list", store.all() == [])
    check("processing a removed vessel is a no-op, not a crash",
          monitor.process(vid, schedule_reading()) == [])

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Vessel monitor OK - all checks passed.")
