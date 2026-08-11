"""BNCT scraping, matching, and monitoring (PRD Section 15).

Parser runs against saved copies of REAL monitoring fragments captured from
portal.bnct-id.com on 2026-07-21 (tests/fixtures/bnct), so it is deterministic
and needs no network. The monitor half uses a temp database.
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.context import AppContext
from bot_kalung.services import bnct
from bot_kalung.services.bnct import BnctReading, BnctVessel
from bot_kalung.services.bnct_monitor import BnctMonitor
from bot_kalung.services.shipments import Shipments

FX = Path(__file__).resolve().parent / "fixtures" / "bnct"
failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def load(name):
    return (FX / name).read_text(encoding="utf-8")


NOW = datetime(2026, 7, 21, 9, 0, 0)


# ---- schedule parsing ------------------------------------------------------
sched = bnct.parse_schedule(load("schedule_ptp.html"), "ptp")
check("schedule cards parsed", len(sched) == 5)

mtt = next((v for v in sched if "MTT REYA" in v.name), None)
check("a scheduled vessel is found by name", mtt is not None)
check("voyage split into in/out",
      mtt.voyage_in == "26RY123S" and mtt.voyage_out == "26RY123N")
check("schedule ETD parsed", mtt.etd == "25/07/2026 12:00")
check("open billing parsed", mtt.open_billing == "21/07/2026 07:00")
check("open stacking parsed", mtt.open_stacking == "21/07/2026 09:00")
check("a scheduled vessel has no loading numbers", mtt.loading_remain is None)
check("phase is schedule", mtt.phase == "schedule")
check("clossing parsed", bool(mtt.clossing) and "/" in mtt.clossing)
check("clossing reefer parsed",
      bool(mtt.clossing_reefer) and "/" in mtt.clossing_reefer)

# Voyage numbers can contain a hyphen (EVER CONCERT: "0798-087S - 0798-087N").
# The in/out separator is " - ", not the first hyphen, so the outbound must be
# the whole "0798-087N", not "087S - 0798-087N".
vin, vout = bnct._split_voyage("Voyage 0798-087S - 0798-087N")
check("hyphenated voyage splits on the spaced separator",
      vin == "0798-087S" and vout == "0798-087N")
_ever = bnct.BnctVessel("ptp", "schedule", "MV. EVER CONCERT",
                        "0798-087S", "0798-087N")
check("an edited DO (EVER CONCERT / 0798-087N) matches the schedule entry",
      bnct.match_vessel([_ever], "EVER CONCERT", "0798-087N") is _ever)

# ---- alongside parsing -----------------------------------------------------
along = bnct.parse_alongside(load("alongside_tpkb.html"), "tpkb")
check("alongside card parsed", len(along) == 1)
hua = along[0]
check("alongside ATB parsed", hua.atb == "20/07/2026 07:11")
check("loading totals are the last matrix column",
      hua.loading_plan == 834 and hua.loading_actual == 203
      and hua.loading_remain == 631)
check("discharge totals parsed",
      hua.discharge_plan == 763 and hua.discharge_actual == 746
      and hua.discharge_remain == 17)
check("restow totals parsed",
      hua.restow_plan == 0 and hua.restow_remain == 0)
check("loading percentage computed", hua.loading_pct == 24)  # 203/834
check("not departing while remain is high", not hua.departing)

# an empty site returns nothing rather than raising
check("DATA NOT AVAILABLE yields no vessels",
      bnct.parse_alongside(load("alongside_ptp.html"), "ptp") == [])

# ---- matching --------------------------------------------------------------
allv = (sched + along
        + bnct.parse_schedule(load("schedule_tpkb.html"), "tpkb"))
check("app 'MTT REYA'/'123N' matches 'MV. MTT REYA'/'26RY123N'",
      bnct.match_vessel(allv, "MTT REYA", "123N") is mtt)
check("wrong voyage does not match",
      bnct.match_vessel(allv, "MTT REYA", "999X") is None)
check("unknown vessel does not match",
      bnct.match_vessel(allv, "GHOST SHIP", "123N") is None)
check("name prefix MV. is ignored",
      bnct.normalize_name("MV. MTT REYA") == bnct.normalize_name("MTT REYA"))

# alongside beats schedule when a vessel is in both phases
both = [BnctVessel("tpkb", "schedule", "TEST", "001S", "001N"),
        BnctVessel("tpkb", "alongside", "TEST", "001S", "001N")]
check("alongside phase wins over schedule",
      bnct.match_vessel(both, "TEST", "001N").phase == "alongside")
check("a 2-char voyage is too short to match (avoids false hits)",
      bnct.match_vessel(both, "TEST", "1N") is None)

# ---- reading ----------------------------------------------------------------
r = bnct.read_for_shipment(allv, "MTT REYA", "123N", now=NOW)
check("reading found and scheduled", r.found and r.phase == "schedule")
check("reading carries the timestamp", r.checked_at == NOW.isoformat())
missing = bnct.read_for_shipment(allv, "NO SUCH", "000", now=NOW)
check("missing vessel reads as not found", not missing.found
      and missing.phase is None and "belum terjadwal" in missing.note)

# departing threshold
low = BnctVessel("tpkb", "alongside", "X", "1S", "1N", loading_remain=3,
                 loading_plan=800)
check("remain below 5 is departing", low.departing)

# ---- a vessel at both terminals is combined (real EVER CONCERT 088N) --------
# ptp handles discharge only (loading 0); tpkb handles loading only (discharge 0).
ptp = BnctVessel("ptp", "alongside", "EVER CONCERT (S978)", "0800-088S", "0800-088N",
                 loading_plan=0, loading_actual=0, loading_remain=0,
                 discharge_plan=406, discharge_actual=82, discharge_remain=324,
                 atb="10/08/2026 15:15", etd="12/08/2026 05:00")
tpkb = BnctVessel("tpkb", "alongside", "MV. EVER CONCERT", "0800-088S", "0800-088N",
                  loading_plan=399, loading_actual=0, loading_remain=399,
                  discharge_plan=0, discharge_actual=0, discharge_remain=0,
                  atb="10/08/2026 15:08", etd="12/08/2026 05:00")
check("the discharge-only terminal alone would look departing", ptp.departing)
combined = bnct.match_vessel([ptp, tpkb], "EVER CONCERT", "088N")
check("loading numbers come from the loading terminal",
      combined.loading_plan == 399 and combined.loading_remain == 399)
check("discharge numbers come from the discharge terminal",
      combined.discharge_plan == 406 and combined.discharge_remain == 324)
check("the combined vessel is NOT falsely departing", not combined.departing)
check("a single match is returned unchanged (identity)",
      bnct.match_vessel([ptp], "EVER CONCERT", "088N") is ptp)
r_both = bnct.read_for_shipment([ptp, tpkb], "EVER CONCERT", "088N", now=NOW)
check("read_for_shipment combines both terminals",
      r_both.found and not r_both.departing
      and r_both.vessel.loading_remain == 399
      and r_both.vessel.discharge_remain == 324)

# ---- shared transition helper (used by both shipment and vessel monitors) --
from bot_kalung.services.bnct_monitor import build_notes

_sched = bnct.read_for_shipment(allv, "MTT REYA", "123N", now=NOW)
_notes = build_notes(False, False, False, _sched, "NIT15", "ship-1")
check("build_notes fires schedule on a first sighting",
      len(_notes) == 1 and _notes[0].kind == "schedule"
      and _notes[0].shipment_id == "ship-1")
check("schedule notification body carries Clossing",
      "Clossing" in _notes[0].body)
check("build_notes stays silent once already found",
      build_notes(True, False, False, _sched, "NIT15", "ship-1") == [])

# ---- merged_record carries schedule-only fields forward --------------------
from bot_kalung.services.bnct_monitor import merged_record

_sch_rec = merged_record(BnctReading(True, "schedule", NOW.isoformat(), BnctVessel(
    "ptp", "schedule", "X", "1S", "1N", etd="ETD1", open_billing="OB1",
    open_stacking="OS1", clossing="CL1", clossing_reefer="CLR1")), None)
check("a schedule record keeps its own clossing",
      _sch_rec["clossing"] == "CL1" and _sch_rec["clossing_reefer"] == "CLR1")

_along_rec = merged_record(BnctReading(True, "alongside", NOW.isoformat(), BnctVessel(
    "tpkb", "alongside", "X", "1S", "1N", etd="ETD2",
    loading_plan=800, loading_remain=550,
    discharge_plan=700, discharge_remain=20)), _sch_rec)
check("alongside carries clossing/billing/stack forward from the schedule record",
      _along_rec["clossing"] == "CL1" and _along_rec["open_billing"] == "OB1"
      and _along_rec["open_stacking"] == "OS1")
check("alongside keeps its own discharge and loading",
      _along_rec["discharge_remain"] == 20 and _along_rec["loading_remain"] == 550)
check("a field the alongside reading has is not overridden by carry-forward",
      _along_rec["etd"] == "ETD2")

# ---- monitor: persistence and transitions ----------------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)
    ctx = AppContext()
    ctx.create(root)
    shipments = Shipments(ctx.db)
    monitor = BnctMonitor(ctx.db)

    sid = shipments.create({
        "exporter_code": "NIT", "sequence_number": 15,
        "vessel_name": "MTT REYA", "voyage": "123N",
        "booking_number": "T22854", "etd_belawan": "2026-07-25",
        "destination_port": "CHENNAI", "destination_country": "INDIA",
        "container_quantity": 2, "container_size_short": "40'",
    })
    other = shipments.create({
        "exporter_code": "AMJ", "sequence_number": 24, "vessel_name": "INTEGRA",
        "voyage": "181E", "booking_number": "1", "etd_belawan": "2026-08-01",
        "destination_port": "KARACHI", "destination_country": "PAKISTAN",
        "container_quantity": 1, "container_size_short": "40'",
    })

    monitored = {row["id"] for row in monitor.monitored()}
    check("active shipments with a vessel are monitored",
          sid in monitored and other in monitored)

    shipments.set_step(sid, "D2", True)
    check("a departed shipment (D2 done) is no longer monitored",
          sid not in {row["id"] for row in monitor.monitored()})
    shipments.set_step(sid, "D2", False)

    # First poll: vessel is in the schedule -> one 'schedule' notification.
    reading = bnct.read_for_shipment(allv, "MTT REYA", "123N", now=NOW)
    notes = monitor.process(sid, "NIT15", reading)
    check("first schedule sighting notifies once", len(notes) == 1
          and notes[0].kind == "schedule")
    check("schedule notification carries the ETD", "25/07/2026" in notes[0].body)
    check("check was recorded", monitor.latest(sid)["found"] == 1)

    # Second poll, still scheduled: no repeat notification.
    notes = monitor.process(sid, "NIT15", reading)
    check("no repeat notification while nothing changes", notes == [])
    check("but the check is still recorded (every check kept)",
          len(monitor.history(sid)) == 2)

    # Vessel moves alongside.
    alongside_reading = BnctReading(
        found=True, phase="alongside", checked_at=NOW.isoformat(),
        vessel=BnctVessel("tpkb", "alongside", "MV. MTT REYA", "26RY123S",
                          "26RY123N", loading_plan=800, loading_actual=250,
                          loading_remain=550, discharge_remain=10))
    notes = monitor.process(sid, "NIT15", alongside_reading)
    check("moving alongside notifies", any(n.kind == "alongside" for n in notes))
    check("alongside totals persisted",
          monitor.latest(sid)["loading_remain"] == 550)
    check("clossing carried forward into the alongside check",
          bool(monitor.latest(sid)["clossing"]))

    # Loading nearly done -> departing alert, once.
    departing_reading = BnctReading(
        found=True, phase="alongside", checked_at=NOW.isoformat(),
        vessel=BnctVessel("tpkb", "alongside", "MV. MTT REYA", "26RY123S",
                          "26RY123N", loading_plan=800, loading_actual=798,
                          loading_remain=2))
    notes = monitor.process(sid, "NIT15", departing_reading)
    check("crossing the threshold fires the departing alert",
          any(n.kind == "departing" for n in notes))
    check("departing message tells them to pay LOLO",
          any("LOLO" in n.body for n in notes if n.kind == "departing"))
    notes = monitor.process(sid, "NIT15", departing_reading)
    check("departing alert does not repeat", notes == [])

    # A not-found reading is recorded too, so the UI can show "not found yet".
    nf = bnct.read_for_shipment(allv, "GHOST", "000", now=NOW)
    monitor.process(other, "AMJ24", nf)
    check("not-found reading recorded for the other shipment",
          monitor.latest(other)["found"] == 0)

    # Deleting the shipment cascades its checks away.
    ctx.db.execute("DELETE FROM shipments WHERE id=?", (sid,))
    check("checks cascade on shipment delete",
          ctx.db.query_one("SELECT COUNT(*) c FROM bnct_checks "
                           "WHERE shipment_id=?", (sid,))["c"] == 0)

# ---- controller: polling without touching the network ----------------------
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from bot_kalung.ui.bnct_controller import BnctController, interval_minutes

app = QApplication.instance() or QApplication([])


class FakeClient:
    def __init__(self, vessels):
        self.vessels = vessels
        self.calls = 0

    def fetch_vessels(self):
        self.calls += 1
        return list(self.vessels)


def pump(controller):
    """Run the event loop until one poll cycle finishes."""
    loop = QEventLoop()
    controller.polled.connect(loop.quit)
    QTimer.singleShot(10_000, loop.quit)   # safety net
    controller.poll_now()
    loop.exec()


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)
    ctx = AppContext()
    ctx.create(root)
    shipments = Shipments(ctx.db)
    sid = shipments.create({
        "exporter_code": "NIT", "sequence_number": 15, "vessel_name": "MTT REYA",
        "voyage": "123N", "booking_number": "T22854", "etd_belawan": "2026-07-25",
        "destination_port": "CHENNAI", "destination_country": "INDIA",
        "container_quantity": 2, "container_size_short": "40'",
    })

    ctx.settings.set("bnct_interval_minutes", "5")
    check("interval read from settings", interval_minutes(ctx.settings) == 5)
    ctx.settings.set("bnct_interval_minutes", "0")
    check("interval clamped to a floor of 1", interval_minutes(ctx.settings) == 1)

    client = FakeClient(allv)
    controller = BnctController(ctx.db, ctx.settings, client=client)
    notes = []
    checked = []
    controller.notified.connect(notes.append)
    controller.checked.connect(checked.append)

    pump(controller)
    check("controller fetched once", client.calls == 1)
    check("controller recorded a check for the shipment", checked == [sid])
    check("controller emitted the schedule notification",
          any(n.kind == "schedule" for n in notes))
    check("check landed in the database",
          BnctMonitor(ctx.db).latest(sid)["found"] == 1)

    # A fetch error must not crash the controller.
    class BoomClient:
        def fetch_vessels(self):
            raise bnct.BnctError("simulated outage")

    controller.client = BoomClient()
    errors = []
    controller.error.connect(errors.append)
    pump(controller)
    check("a fetch error is surfaced, not raised",
          errors and "simulated outage" in errors[0])

    controller.stop()

    # ---- the "Memeriksa BNCT..." banner resolves after a manual check -------
    from bot_kalung.ui.main_window import MainWindow

    ctx.settings.set("setup_complete", "1")
    window = MainWindow(ctx)
    window.bnct.client = FakeClient(allv)
    window.open_shipment(sid)

    window._bnct_poll_now()
    check("the checking banner shows immediately",
          "Memeriksa BNCT" in window.detail.message.text())
    pump(window.bnct)
    check("the banner resolves once the check completes",
          "Memeriksa BNCT" not in window.detail.message.text()
          and "selesai" in window.detail.message.text())

    # The banner is dismissable via its ✕ button.
    check("the banner offers a dismiss button",
          not window.detail.message._close.isHidden())
    window.detail.message._close.click()
    check("dismissing hides the banner",
          window.detail.message.isHidden()
          and window.detail.message._close.isHidden())

    # A background (non-manual) poll must not touch the detail banner.
    window.detail.message.show_info("pesan lain")
    pump(window.bnct)
    check("an automatic poll leaves an unrelated banner alone",
          window.detail.message.text() == "pesan lain")

    # On a fetch error, the banner reports it instead of hanging on "checking".
    window.bnct.client = BoomClient()
    window._bnct_poll_now()
    pump(window.bnct)
    check("a failed manual check surfaces the error in the banner",
          "simulated outage" in window.detail.message.text())

    # A brand-new shipment must be checked at once, not up to an interval
    # later — its vessel is often already on the schedule.
    window.bnct.client = FakeClient(allv)
    # Offscreen normally blocks live polling so tests never hit the portal;
    # opt in explicitly now that the client is a fake.
    window._bnct_live = True
    polls = []
    window.bnct.polled.connect(lambda: polls.append(1))
    window._on_shipment_created(sid)
    pump(window.bnct)
    check("creating a shipment polls BNCT straight away", bool(polls))
    window._bnct_live = False

    # And the monitor covers every active shipment, not just one.
    second = shipments.create({
        "exporter_code": "AMJ", "sequence_number": 30,
        "vessel_name": "GREEN OWL", "voyage": "629N",
        "booking_number": "B2", "etd_belawan": "2026-08-01",
        "destination_port": "KARACHI", "destination_country": "PAKISTAN",
        "container_quantity": 1, "container_size_short": "40'"})
    watched = {r["id"] for r in BnctMonitor(ctx.db).monitored()}
    check("every active shipment with a vessel is polled",
          {sid, second} <= watched)

    window.bnct.stop()
    window.wizard.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("BNCT OK - all checks passed.")
