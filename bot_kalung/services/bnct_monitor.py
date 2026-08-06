"""Persist BNCT checks and decide when to notify (PRD Section 15).

Separate from `bnct.py` (the pure scraper) because this layer touches the
database and the workflow. Each poll produces one `BnctReading` per monitored
shipment; `process()` stores it and returns the notifications that the poll
*newly* warrants — transitions only, so a vessel sitting alongside for hours
does not re-alert every five minutes.

Notifications:
* `schedule` — the vessel first appears in the BNCT schedule (ETD/billing/stack).
* `alongside` — it moves to the berth and work begins.
* `departing` — Loading Remain Total drops below the PRD threshold; the crew
  must pay LOLO in full to Indra.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.db import Database, new_id
from . import bnct
from .bnct import BnctReading
from .notifications import NotificationStore


@dataclass
class Notification:
    kind: str            # "schedule" | "alongside" | "departing"
    shipment_id: str | None   # None for a standalone monitored vessel
    title: str
    body: str


def build_notes(prev_found: bool, prev_alongside: bool, prev_departing: bool,
                reading: BnctReading, label: str,
                shipment_id: str | None) -> list[Notification]:
    """The notifications a reading newly warrants, given the previous state.

    Shared by the shipment monitor and the standalone vessel monitor so both
    alert on the same transitions (first seen → alongside → departing). `label`
    prefixes the message ("AMJ24" for a shipment, "EVER CONCERT 088N" for a
    vessel); `shipment_id` is None for a standalone vessel.
    """
    notes: list[Notification] = []
    v = reading.vessel

    if reading.found and reading.phase == "schedule" and not prev_found:
        notes.append(Notification(
            "schedule", shipment_id, f"{label}: kapal terjadwal di BNCT",
            f"ETD {v.etd or '-'} · Open Billing {v.open_billing or '-'} · "
            f"Open Stack {v.open_stacking or '-'}"))

    if reading.phase == "alongside" and not prev_alongside:
        notes.append(Notification(
            "alongside", shipment_id, f"{label}: kapal sudah sandar",
            f"Loading sisa {_fmt(v.loading_remain)} dari "
            f"{_fmt(v.loading_plan)} · Discharge sisa "
            f"{_fmt(v.discharge_remain)}"))

    if reading.departing and not prev_departing:
        notes.append(Notification(
            "departing", shipment_id, f"{label}: kapal akan berangkat",
            f"Loading sisa {_fmt(v.loading_remain)} kontainer — bayar LOLO "
            "penuh ke Indra."))

    return notes


class BnctMonitor:
    def __init__(self, db: Database):
        self.db = db
        self.notifications = NotificationStore(db)

    # -- which shipments to poll ------------------------------------------

    def monitored(self) -> list:
        """Active shipments still awaiting departure (step D2 not yet ticked)
        that carry a vessel name to match on.
        """
        return self.db.query(
            "SELECT s.* FROM shipments s "
            "WHERE s.status='active' AND s.vessel_name IS NOT NULL "
            "AND s.vessel_name <> '' AND NOT EXISTS ("
            "  SELECT 1 FROM workflow_steps w WHERE w.shipment_id=s.id "
            "  AND w.step_code='D2' AND w.status='complete')")

    # -- reads -------------------------------------------------------------

    def latest(self, shipment_id: str):
        return self.db.query_one(
            "SELECT * FROM bnct_checks WHERE shipment_id=? "
            "ORDER BY checked_at DESC, rowid DESC LIMIT 1", (shipment_id,))

    def history(self, shipment_id: str, limit: int = 50) -> list:
        return self.db.query(
            "SELECT * FROM bnct_checks WHERE shipment_id=? "
            "ORDER BY checked_at DESC, rowid DESC LIMIT ?", (shipment_id, limit))

    # -- writes ------------------------------------------------------------

    def record(self, shipment_id: str, reading: BnctReading) -> None:
        v = reading.vessel
        self.db.execute(
            "INSERT INTO bnct_checks (id, shipment_id, checked_at, found, phase, "
            "site, etd, open_billing, open_stacking, atb, berth, loading_plan, "
            "loading_actual, loading_remain, discharge_plan, discharge_actual, "
            "discharge_remain, restow_plan, restow_actual, restow_remain, "
            "departing, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id(), shipment_id, reading.checked_at, 1 if reading.found else 0,
             reading.phase, v.site if v else None,
             v.etd if v else None, v.open_billing if v else None,
             v.open_stacking if v else None, v.atb if v else None,
             v.berth if v else None,
             v.loading_plan if v else None, v.loading_actual if v else None,
             v.loading_remain if v else None,
             v.discharge_plan if v else None, v.discharge_actual if v else None,
             v.discharge_remain if v else None,
             v.restow_plan if v else None, v.restow_actual if v else None,
             v.restow_remain if v else None,
             1 if reading.departing else 0, reading.note))

    def process(self, shipment_id: str, label: str,
                reading: BnctReading) -> list[Notification]:
        """Store the reading and return notifications for state it newly enters.

        `label` is the shipment's short id (e.g. "AMJ24") for the message text.
        """
        prev = self.latest(shipment_id)
        notes = build_notes(
            bool(prev and prev["found"]),
            bool(prev and prev["phase"] == "alongside"),
            bool(prev and prev["departing"]),
            reading, label, shipment_id)

        self.record(shipment_id, reading)
        # Persist each transition so it survives the tray toast and drives the
        # in-app notification centre. Done here, with the check, so only the
        # app instance that actually detects the transition writes it.
        for note in notes:
            self.notifications.add(note.kind, shipment_id, note.title,
                                   note.body, created_at=reading.checked_at)
        return notes


def _fmt(value) -> str:
    return "-" if value is None else str(value)


def build_reading(row, vessels) -> BnctReading:
    """Reading for one shipment row from a fetched vessel list."""
    return bnct.read_for_shipment(
        vessels, row["vessel_name"] or "", row["voyage"] or "")
