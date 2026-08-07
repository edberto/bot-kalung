"""Standalone vessel monitoring — the "Monitor Kapal" screen.

Reuses the BNCT scraper (`bnct.py`) and the transition logic (`bnct_monitor`)
but tracks vessels the user enters by name + voyage, with no shipment behind
them. Each vessel's previous reading is kept on its own row (`monitored_vessels`)
so transitions are detected without a separate history table.
"""

from __future__ import annotations

import json
from datetime import datetime

from ..core.db import Database, new_id
from .bnct import BnctReading
from .bnct_monitor import Notification, build_notes, merged_record
from .notifications import NotificationStore


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class MonitoredVessels:
    """CRUD for the standalone vessels being watched. Never raises on write."""

    def __init__(self, db: Database):
        self.db = db

    def add(self, vessel_name: str, voyage: str = "") -> str:
        vid = new_id()
        self.db.execute(
            "INSERT INTO monitored_vessels (id, vessel_name, voyage, created_at) "
            "VALUES (?,?,?,?)",
            (vid, vessel_name.strip(), voyage.strip(), _now()))
        return vid

    def all(self) -> list:
        return self.db.query(
            "SELECT * FROM monitored_vessels ORDER BY created_at DESC")

    def monitored(self) -> list:
        """Everything still being watched — vessels stay until removed by hand."""
        return self.all()

    def get(self, vessel_id: str):
        return self.db.query_one(
            "SELECT * FROM monitored_vessels WHERE id=?", (vessel_id,))

    def delete(self, vessel_id: str) -> None:
        self.db.execute("DELETE FROM monitored_vessels WHERE id=?", (vessel_id,))

    def record_reading(self, vessel_id: str, reading: BnctReading,
                       summary: str, record_json: str) -> None:
        self.db.execute(
            "UPDATE monitored_vessels SET last_checked_at=?, last_found=?, "
            "last_phase=?, last_departing=?, last_summary=?, last_reading=? "
            "WHERE id=?",
            (reading.checked_at, 1 if reading.found else 0, reading.phase,
             1 if reading.departing else 0, summary, record_json, vessel_id))


def summarise(reading: BnctReading) -> str:
    """A one-line Indonesian status for the card."""
    if not reading.found:
        return "Belum ditemukan di BNCT"
    v = reading.vessel
    if reading.departing:
        return f"Akan berangkat — Loading sisa {v.loading_remain} — bayar LOLO"
    if reading.phase == "alongside":
        return (f"Sudah sandar · Loading sisa {v.loading_remain} dari "
                f"{v.loading_plan}")
    return (f"Terjadwal · ETD {v.etd or '-'} · Open Stack "
            f"{v.open_stacking or '-'}")


class VesselMonitor:
    """Turns a fetched vessel list into transition notifications for the
    standalone monitored vessels, mirroring `BnctMonitor` for shipments."""

    def __init__(self, db: Database):
        self.db = db
        self.vessels = MonitoredVessels(db)
        self.notifications = NotificationStore(db)

    def monitored(self) -> list:
        return self.vessels.monitored()

    def process(self, vessel_id: str, reading: BnctReading) -> list[Notification]:
        row = self.vessels.get(vessel_id)
        if row is None:
            return []

        prev = _prev_reading(row)
        label = f"{row['vessel_name']} {row['voyage'] or ''}".strip()
        notes = build_notes(
            bool(row["last_found"]),
            row["last_phase"] == "alongside",
            bool(row["last_departing"]),
            reading, label, shipment_id=None)

        record = merged_record(reading, prev)
        self.vessels.record_reading(
            vessel_id, reading, summarise(reading), json.dumps(record))
        for note in notes:
            self.notifications.add(note.kind, None, note.title, note.body,
                                   created_at=reading.checked_at)
        return notes


def _prev_reading(row) -> dict | None:
    """The previous stored reading snapshot for carry-forward, or None."""
    try:
        raw = row["last_reading"]
    except (KeyError, IndexError):
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None
