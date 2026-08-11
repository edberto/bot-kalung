"""Standalone vessel monitoring — the "Monitor Kapal" screen.

Reuses the BNCT scraper (`bnct.py`) and the transition logic (`bnct_monitor`)
but tracks vessels the user enters, with no shipment behind them. Each vessel is
monitored across a rolling window of 3 upcoming voyages: adding a vessel with one
starting voyage auto-fills the next two, and each time a voyage departs the next
one rolls in (the number in the voyage is incremented). Departed voyages are kept
as history until removed by hand.

The per-voyage state (`monitored_vessels.state`) is a strict forward-only machine
notfound → scheduled → berthed → departed: it jumps to whatever BNCT currently
shows but never moves backward.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from ..core.db import Database, new_id
from .bnct import BnctReading
from .bnct_monitor import Notification, build_notes, merged_record
from .notifications import NotificationStore

# Strict forward-only lifecycle. The kanban columns key off these same names.
_RANK = {"notfound": 0, "scheduled": 1, "berthed": 2, "departed": 3}
_BY_RANK = {rank: name for name, rank in _RANK.items()}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def next_voyage(voyage: str) -> str:
    """Increment the last run of digits, preserving its width.

    N379 -> N380, 182E -> 183E, 123N -> 124N, 26RY123N -> 26RY124N. A voyage with
    no digits is returned unchanged (it cannot be rolled forward).
    """
    runs = list(re.finditer(r"\d+", voyage or ""))
    if not runs:
        return voyage
    last = runs[-1]
    incremented = str(int(last.group()) + 1).zfill(len(last.group()))
    return voyage[:last.start()] + incremented + voyage[last.end():]


def _voyage_int(voyage: str) -> int:
    """The last run of digits as an int, for picking the highest voyage."""
    runs = re.findall(r"\d+", voyage or "")
    return int(runs[-1]) if runs else -1


def state_of(row) -> str:
    """The stored strict state, or the legacy derivation for a pre-state row."""
    try:
        state = row["state"]
    except (KeyError, IndexError):
        state = None
    if state:
        return state
    if row["last_departing"]:
        return "departed"
    if row["last_phase"] == "alongside":
        return "berthed"
    if row["last_found"]:
        return "scheduled"
    return "notfound"


def advance_state(current: str, reading: BnctReading) -> str:
    """The new state: the furthest-forward of the current and observed states.

    Never regresses. A berthed/departed vessel that vanishes from BNCT counts as
    departed (it sailed); a scheduled vessel that vanishes stays scheduled.
    """
    current = current or "notfound"
    if reading.departing:
        observed = 3
    elif reading.found:
        observed = 2 if reading.phase == "alongside" else 1
    elif current in ("berthed", "departed"):
        observed = 3
    else:
        observed = 0
    return _BY_RANK[max(_RANK.get(current, 0), observed)]


class MonitoredVessels:
    """CRUD + the rolling 3-voyage window. Never raises on write."""

    def __init__(self, db: Database):
        self.db = db

    def add(self, vessel_name: str, voyage: str = "") -> str:
        vid = new_id()
        self.db.execute(
            "INSERT INTO monitored_vessels (id, vessel_name, voyage, created_at) "
            "VALUES (?,?,?,?)",
            (vid, vessel_name.strip(), voyage.strip(), _now()))
        return vid

    def add_vessel(self, vessel_name: str, start_voyage: str,
                   window: int = 3) -> str:
        """Start monitoring a vessel from `start_voyage`, filling the window."""
        vid = self.add(vessel_name, start_voyage)
        self.ensure_window(vessel_name.strip(), window)
        return vid

    def ensure_window(self, vessel_name: str, target: int = 3) -> None:
        """Top up to `target` non-departed voyages by adding the next voyage(s)
        above the group's current highest. Deleting/departing a voyage refills.

        Never raises: a transient DB error just leaves the window short, and the
        next poll tops it up again.
        """
        try:
            rows = self._group_rows(vessel_name)
            voyages = [r["voyage"] for r in rows if r["voyage"]]
            if not voyages:
                return
            non_departed = sum(1 for r in rows if state_of(r) != "departed")
            seen = {v.upper() for v in voyages}
            guard = 0
            while non_departed < target and guard < 2 * target + 4:
                guard += 1
                nxt = next_voyage(max(voyages, key=_voyage_int))
                if not nxt or nxt.upper() in seen:
                    break                  # no digits / would duplicate
                self.add(vessel_name.strip(), nxt)
                voyages.append(nxt)
                seen.add(nxt.upper())
                non_departed += 1
        except Exception:                  # noqa: BLE001 - monitoring must not crash
            return

    def all(self) -> list:
        return self.db.query(
            "SELECT * FROM monitored_vessels ORDER BY created_at DESC")

    def monitored(self) -> list:
        """Every voyage still being watched — they stay until removed by hand."""
        return self.all()

    def groups(self) -> dict[str, list]:
        """{vessel_name: [rows sorted by voyage]} for the manage dialog."""
        grouped: dict[str, list] = {}
        for row in self.all():
            grouped.setdefault(row["vessel_name"], []).append(row)
        for rows in grouped.values():
            rows.sort(key=lambda r: (_voyage_int(r["voyage"]), r["voyage"] or ""))
        return dict(sorted(grouped.items(), key=lambda kv: kv[0].lower()))

    def _group_rows(self, vessel_name: str) -> list:
        return self.db.query(
            "SELECT * FROM monitored_vessels WHERE vessel_name=?",
            (vessel_name.strip(),))

    def get(self, vessel_id: str):
        return self.db.query_one(
            "SELECT * FROM monitored_vessels WHERE id=?", (vessel_id,))

    def delete(self, vessel_id: str) -> None:
        self.db.execute("DELETE FROM monitored_vessels WHERE id=?", (vessel_id,))

    def delete_vessel(self, vessel_name: str) -> None:
        self.db.execute("DELETE FROM monitored_vessels WHERE vessel_name=?",
                        (vessel_name.strip(),))

    def record_reading(self, vessel_id: str, reading: BnctReading, summary: str,
                       record_json: str, state: str) -> None:
        self.db.execute(
            "UPDATE monitored_vessels SET last_checked_at=?, last_found=?, "
            "last_phase=?, last_departing=?, last_summary=?, last_reading=?, "
            "state=? WHERE id=?",
            (reading.checked_at, 1 if reading.found else 0, reading.phase,
             1 if reading.departing else 0, summary, record_json, state,
             vessel_id))


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

        old_state = state_of(row)
        new_state = advance_state(old_state, reading)

        record = merged_record(reading, prev)
        self.vessels.record_reading(
            vessel_id, reading, summarise(reading), json.dumps(record), new_state)

        # Keep the vessel's 3-voyage window topped up on every poll: it rolls the
        # next voyage in after a departure and self-heals a window left short
        # (e.g. an add that failed mid-way).
        self.vessels.ensure_window(row["vessel_name"])

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
