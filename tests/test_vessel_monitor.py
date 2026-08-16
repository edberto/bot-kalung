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
    MonitoredVessels, VesselMonitor, _voyage_int, advance_state, next_voyage,
    state_of, summarise,
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
    # Departure now needs Loading, Restow AND Discharge Remaining all near zero,
    # so discharge winds down with loading — a low `remain` is a departing vessel.
    return BnctReading(True, "alongside", NOW, BnctVessel(
        "tpkb", "alongside", "MV. EVER CONCERT", "0800-088S", "0800-088N",
        loading_plan=800, loading_actual=800 - remain, loading_remain=remain,
        discharge_plan=700, discharge_remain=min(20, remain)))


def notfound_reading():
    return BnctReading(False, None, NOW, None, note="belum terjadwal")


# ---- voyage increment (pure) ----------------------------------------------
check("next_voyage: N379 -> N380", next_voyage("N379") == "N380")
check("next_voyage: 182E -> 183E", next_voyage("182E") == "183E")
check("next_voyage: 123N -> 124N", next_voyage("123N") == "124N")
check("next_voyage: increments the last run only (26RY123N)",
      next_voyage("26RY123N") == "26RY124N")
check("next_voyage: preserves zero-padding", next_voyage("N009") == "N010")
check("next_voyage: no digits is unchanged", next_voyage("ABC") == "ABC")
check("_voyage_int reads the last run", _voyage_int("26RY123N") == 123)

# ---- strict state machine (pure) — jump forward, never backward ------------
check("notfound + schedule -> scheduled",
      advance_state("notfound", schedule_reading()) == "scheduled")
check("notfound seen already alongside jumps to berthed",
      advance_state("notfound", alongside_reading()) == "berthed")
check("scheduled + alongside -> berthed",
      advance_state("scheduled", alongside_reading()) == "berthed")
check("scheduled that vanishes stays scheduled (never regresses)",
      advance_state("scheduled", notfound_reading()) == "scheduled")
check("berthed that vanishes -> departed",
      advance_state("berthed", notfound_reading()) == "departed")
check("berthed seeing schedule again stays berthed (never regresses)",
      advance_state("berthed", schedule_reading()) == "berthed")
check("departed is terminal",
      advance_state("departed", notfound_reading()) == "departed")
check("a departing reading -> departed",
      advance_state("berthed", alongside_reading(remain=2)) == "departed")


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

    # A departure only alerts when the voyage carries an active shipment, and the
    # body names those shipments instead of the generic "pay LOLO" instruction.
    from bot_kalung.core.db import new_id as _new_id
    ctx.db.execute(
        "INSERT INTO shipments (id, exporter_code, sequence_number, vessel_name, "
        "voyage, status, created_at) VALUES (?,?,?,?,?, 'active', '2026-08-14')",
        (_new_id(), "NIT", 16, "EVER CONCERT", "0800-088N"))

    notes = monitor.process(vid, alongside_reading(remain=2))
    check("crossing the threshold fires the departing alert",
          any(n.kind == "departing" for n in notes))
    check("the departing body names the active shipment, not LOLO",
          any("NIT16" in n.body and "LOLO" not in n.body
              for n in notes if n.kind == "departing"))
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
    store.delete_vessel("EVER CONCERT")   # clears the voyage + any rolled-in ones
    check("removing a vessel clears all its voyages", store.all() == [])
    check("processing a removed vessel is a no-op, not a crash",
          monitor.process(vid, schedule_reading()) == [])

    # ---- a berthed vessel that vanishes from BNCT has departed -------------
    from bot_kalung.ui.vessel_monitor_view import vessel_status

    gone = store.add("GONE SHIP", "7N")
    monitor.process(gone, schedule_reading())
    monitor.process(gone, alongside_reading())
    check("a berthed vessel buckets as berthed",
          vessel_status(store.get(gone)) == "berthed")
    monitor.process(gone, notfound_reading())          # vanishes from BNCT
    check("a berthed vessel gone from BNCT is marked departed",
          state_of(store.get(gone)) == "departed")
    check("it buckets into 'sudah berangkat'",
          vessel_status(store.get(gone)) == "departed")
    monitor.process(gone, notfound_reading())
    check("departed stays sticky across further empty polls",
          vessel_status(store.get(gone)) == "departed")

    never = store.add("NEVER BERTH", "8N")
    monitor.process(never, schedule_reading())
    monitor.process(never, notfound_reading())         # gone before berthing
    check("a scheduled vessel that vanishes stays scheduled (never regresses)",
          vessel_status(store.get(never)) == "scheduled")


# ---- the kanban board: bucketing + alphabetical sort -----------------------
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

from bot_kalung.ui.vessel_monitor_view import VesselCard, VesselMonitorView

app = QApplication.instance() or QApplication([])

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)
    ctx = AppContext()
    ctx.create(root)
    store = MonitoredVessels(ctx.db)

    def set_state(vid, found=0, phase=None, departing=0):
        ctx.db.execute(
            "UPDATE monitored_vessels SET last_found=?, last_phase=?, "
            "last_departing=? WHERE id=?", (found, phase, departing, vid))

    set_state(store.add("Charlie", "3N"), found=1, phase="schedule")
    set_state(store.add("alpha", "2N"), found=1, phase="schedule")
    set_state(store.add("alpha", "1N"), found=1, phase="schedule")
    set_state(store.add("Delta", "9N"), found=1, phase="alongside")
    set_state(store.add("Echo", "5N"), found=1, phase="alongside", departing=1)
    store.add("Foxtrot", "6N")           # never checked -> not found

    view = VesselMonitorView(ctx.db)     # __init__ calls refresh()

    def names(key):
        return [(r["vessel_name"], r["voyage"]) for r in view.columns[key]["rows"]]

    check("scheduled column holds the three scheduled vessels",
          len(view.columns["scheduled"]["rows"]) == 3)
    check("scheduled sorted by name then voyage (case-insensitive)",
          names("scheduled") == [("alpha", "1N"), ("alpha", "2N"), ("Charlie", "3N")])
    check("berthed column holds the alongside vessel", names("berthed") == [("Delta", "9N")])
    check("departed column holds the departing vessel", names("departed") == [("Echo", "5N")])
    check("not-found column holds the unchecked vessel",
          names("notfound") == [("Foxtrot", "6N")])
    check("headers carry the count", "(3)" in view.columns["scheduled"]["header"].text())
    check("every vessel rendered as a card",
          len(view.findChildren(VesselCard)) == 6)


# ---- rolling 3-voyage window ------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)
    ctx = AppContext()
    ctx.create(root)
    store = MonitoredVessels(ctx.db)
    monitor = VesselMonitor(ctx.db)

    def voyages(name):
        return sorted(r["voyage"] for r in store._group_rows(name))

    def row_for(name, voy):
        return next(r for r in store._group_rows(name) if r["voyage"] == voy)

    def non_departed(name):
        return sum(1 for r in store._group_rows(name) if state_of(r) != "departed")

    store.add_vessel("WAN HAI 101", "N379")
    check("adding a vessel fills a 3-voyage window",
          voyages("WAN HAI 101") == ["N379", "N380", "N381"])

    n379 = row_for("WAN HAI 101", "N379")["id"]
    monitor.process(n379, schedule_reading())
    monitor.process(n379, alongside_reading())
    monitor.process(n379, alongside_reading(remain=2))       # N379 departs
    check("a departed voyage rolls the next one in (N382)",
          voyages("WAN HAI 101") == ["N379", "N380", "N381", "N382"])
    check("the departed voyage stays as history",
          state_of(row_for("WAN HAI 101", "N379")) == "departed")
    check("the window keeps 3 non-departed voyages", non_departed("WAN HAI 101") == 3)

    store.delete(row_for("WAN HAI 101", "N381")["id"])
    store.ensure_window("WAN HAI 101")
    check("deleting a non-departed voyage refills to 3",
          non_departed("WAN HAI 101") == 3)

    store.add_vessel("INTEGRA", "182E")
    check("a numeric-prefix voyage rolls too (182E/183E/184E)",
          voyages("INTEGRA") == ["182E", "183E", "184E"])
    check("the two vessels are grouped separately",
          set(store.groups()) == {"WAN HAI 101", "INTEGRA"})

    # a poll self-heals a window that was left short (issue: future voyages
    # missing) — any process() tops the vessel back up to 3 non-departed.
    store.delete(row_for("INTEGRA", "184E")["id"])
    check("window temporarily short after a raw delete",
          len(store._group_rows("INTEGRA")) == 2)
    monitor.process(row_for("INTEGRA", "182E")["id"], notfound_reading())
    check("a poll tops the window back up to 3",
          non_departed("INTEGRA") == 3)

    store.delete_vessel("WAN HAI 101")
    check("delete_vessel removes every voyage of that vessel",
          store._group_rows("WAN HAI 101") == [])
    check("the other vessel is untouched", voyages("INTEGRA") == ["182E", "183E", "184E"])

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Vessel monitor OK - all checks passed.")
