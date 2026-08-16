"""Persist BNCT checks and decide when to notify (PRD Section 15).

Separate from `bnct.py` (the pure scraper) because this layer touches the
database and the workflow. Each poll produces one `BnctReading` per monitored
shipment; `process()` stores it and returns the notifications that the poll
*newly* warrants — transitions only, so a vessel sitting alongside for hours
does not re-alert every five minutes.

Notifications:
* `schedule` — the vessel first appears in the BNCT schedule (ETD/billing/stack).
* `alongside` — it moves to the berth and work begins.
* `departing` — Loading, Restow AND Discharge Remain Totals all drop near zero;
  the crew must pay LOLO in full to Indra.
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
            f"ETD {v.etd or '-'} · Clossing {v.clossing or '-'} · "
            f"Clossing Reefer {v.clossing_reefer or '-'} · Open Billing "
            f"{v.open_billing or '-'} · Open Stack {v.open_stacking or '-'}"))

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


# The value columns of a stored check (id/shipment_id/checked_at are separate),
# in a stable order so the INSERT and the JSON snapshot agree.
CHECK_KEYS = (
    "found", "phase", "site", "etd", "open_billing", "open_stacking",
    "clossing", "clossing_reefer", "atb", "berth",
    "loading_plan", "loading_actual", "loading_remain",
    "discharge_plan", "discharge_actual", "discharge_remain",
    "restow_plan", "restow_actual", "restow_remain", "departing", "note",
)

# Fields only the schedule card carries. The alongside card omits them, so they
# are inherited from the previous record — "don't override, append".
_CARRY_KEYS = ("etd", "open_billing", "open_stacking", "clossing", "clossing_reefer")


def _prev_get(prev, key):
    if prev is None:
        return None
    try:
        return prev[key]
    except (KeyError, IndexError):
        return None      # an older row/snapshot predates this column


def merged_record(reading: BnctReading, prev=None) -> dict:
    """The values to store for this reading, keyed like the bnct_checks columns.

    Any blank schedule-only field inherits the previous record's value, so a
    berthed vessel keeps the Clossing/Open-Billing/etc. seen while it was
    scheduled. `prev` may be a bnct_checks row or a decoded JSON snapshot.
    """
    v = reading.vessel
    record = {
        "found": 1 if reading.found else 0,
        "phase": reading.phase,
        "site": v.site if v else None,
        "etd": (v.etd if v else "") or "",
        "open_billing": (v.open_billing if v else "") or "",
        "open_stacking": (v.open_stacking if v else "") or "",
        "clossing": (v.clossing if v else "") or "",
        "clossing_reefer": (v.clossing_reefer if v else "") or "",
        "atb": (v.atb if v else "") or "",
        "berth": (v.berth if v else "") or "",
        "loading_plan": v.loading_plan if v else None,
        "loading_actual": v.loading_actual if v else None,
        "loading_remain": v.loading_remain if v else None,
        "discharge_plan": v.discharge_plan if v else None,
        "discharge_actual": v.discharge_actual if v else None,
        "discharge_remain": v.discharge_remain if v else None,
        "restow_plan": v.restow_plan if v else None,
        "restow_actual": v.restow_actual if v else None,
        "restow_remain": v.restow_remain if v else None,
        "departing": 1 if reading.departing else 0,
        "note": reading.note,
    }
    for key in _CARRY_KEYS:
        if not record[key]:
            carried = _prev_get(prev, key)
            if carried:
                record[key] = carried
    return record


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

    def record(self, shipment_id: str, reading: BnctReading, prev=None) -> None:
        record = merged_record(reading, prev)
        columns = ", ".join(CHECK_KEYS)
        placeholders = ", ".join("?" for _ in CHECK_KEYS)
        self.db.execute(
            f"INSERT INTO bnct_checks (id, shipment_id, checked_at, {columns}) "
            f"VALUES (?, ?, ?, {placeholders})",
            (new_id(), shipment_id, reading.checked_at,
             *(record[key] for key in CHECK_KEYS)))

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
        # Departure is announced once per voyage by the vessel board (listing its
        # shipments), so the per-shipment departure alert is dropped to avoid a
        # duplicate and the generic "pay LOLO" message. The departing state is
        # still recorded on the check below.
        notes = [n for n in notes if n.kind != "departing"]

        self.record(shipment_id, reading, prev)
        self._sync_etd(shipment_id, reading, prev)
        # Persist each transition so it survives the tray toast and drives the
        # in-app notification centre. Done here, with the check, so only the
        # app instance that actually detects the transition writes it.
        for note in notes:
            self.notifications.add(note.kind, shipment_id, note.title,
                                   note.body, created_at=reading.checked_at)
        return notes

    def _sync_etd(self, shipment_id: str, reading: BnctReading, prev) -> None:
        """Converge the shipment's stored ETD onto BNCT's schedule — the
        authoritative departure date, so all shipments on a voyage line up.
        """
        if not reading.found:
            return
        iso = bnct.etd_to_iso(merged_record(reading, prev)["etd"])
        if iso:
            self.db.execute(
                "UPDATE shipments SET etd_belawan=?, etd_source='bnct' WHERE id=?",
                (iso, shipment_id))


def _fmt(value) -> str:
    return "-" if value is None else str(value)


def build_reading(row, vessels) -> BnctReading:
    """Reading for one shipment row from a fetched vessel list."""
    return bnct.read_for_shipment(
        vessels, row["vessel_name"] or "", row["voyage"] or "")
