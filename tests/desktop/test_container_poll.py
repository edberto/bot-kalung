"""The BNCT poll worker's container step: 51-STACK RECEIVING fires one alert.

Exercises _PollWorker._process_containers directly (synchronously, no thread) so
the transition logic is tested without spinning the event loop.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from bot_kalung.core.db import Database, db_path_for, new_id
from bot_kalung.services.bnct import BnctContainer, BnctVessel
from bot_kalung.services.bnct_monitor import BnctMonitor
from bot_kalung.services.containers import Containers
from bot_kalung.services.notifications import NotificationStore
from bot_kalung.services.vessel_monitor import VesselMonitor
from bot_kalung.ui.bnct_controller import _PollWorker

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


class FakeClient:
    def __init__(self, cards):
        self._cards = cards

    def fetch_vessels(self):
        return []

    def fetch_containers_batch(self, numbers):
        return {n: self._cards.get(n, []) for n in numbers}


app = QApplication.instance() or QApplication([])


def blank_result():
    return {"notifications": [], "checked": [], "vessel_checked": [],
            "container_checked": []}


with tempfile.TemporaryDirectory() as tmp:
    db = Database(db_path_for(Path(tmp)))
    db.initialize()
    containers = Containers(db)

    sid = new_id()
    db.execute(
        "INSERT INTO shipments (id, exporter_code, sequence_number, vessel_name, "
        "voyage, status, created_at) VALUES (?,?,?,?,?, 'active', '2026-08-14')",
        (sid, "NIT", 16, "MV.MAO GANG GUANG ZHOU", "021N"))
    containers.populate(sid, ["CMAU8513405"], size="40'")

    stack_card = BnctContainer(
        site="TPKB", container_no="CMAU8513405", size="40", type="HQ",
        status_code="51", status_text="STACK RECEIVING",
        vessel_name="MV.MAO GANG GUANG ZHOU", voyage_in="021S", voyage_out="021N")

    # The vessel is on the schedule (found, not departing) -> containers polled.
    vessels = [BnctVessel(site="tpkb", phase="schedule",
                          name="MV.MAO GANG GUANG ZHOU",
                          voyage_in="021S", voyage_out="021N")]

    worker = _PollWorker(FakeClient({"CMAU8513405": [stack_card]}),
                         BnctMonitor(db), VesselMonitor(db), containers,
                         NotificationStore(db))

    result = blank_result()
    worker._process_containers(vessels, result)

    row = containers.for_shipment(sid)[0]
    check("container status updated to 51 from the poll", row.at_stack_receiving)
    check("the shipment was flagged for a container refresh",
          sid in result["container_checked"])
    notes = result["notifications"]
    check("a container notification fired on the 51 transition",
          len(notes) == 1 and notes[0].kind == "container")
    check("the notification deep-links to the shipment",
          notes[0].shipment_id == sid and "CMAU8513405" in notes[0].title)
    check("the notification was persisted",
          db.query_one("SELECT COUNT(*) c FROM notifications WHERE kind='container'")
          ["c"] == 1)

    # A second poll at the same status must NOT re-notify.
    result2 = blank_result()
    worker._process_containers(vessels, result2)
    check("no duplicate notification while it stays at 51",
          result2["notifications"] == [])

    # When the vessel has departed, its containers are not polled at all.
    departed = [BnctVessel(site="tpkb", phase="alongside",
                           name="MV.MAO GANG GUANG ZHOU", voyage_out="021N",
                           loading_remain=0)]
    result3 = blank_result()
    worker._process_containers(departed, result3)
    check("a departed vessel's containers are skipped",
          result3["container_checked"] == [])

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Container poll OK - all checks passed.")
