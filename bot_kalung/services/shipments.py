"""Shipment and workflow-step persistence (PRD Sections 13.1 and 13.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ..core.constants import ACTION_STATUS_DONE, WORKFLOW_STEPS
from ..core.db import Database, new_id
from .audit import COMPLETED, CREATED, DELETED, AuditLog

# Built-in steps keyed by code -> (ordinal, title, description, phase2_only).
# Ordinal drives the default sort position so the fixed 22 keep their order.
_BUILTIN = {
    code: (i, title, description, phase2_only)
    for i, (code, _phase, title, description, _auto, phase2_only)
    in enumerate(WORKFLOW_STEPS)
}

# Custom steps carry a "custom:" prefix so their code can never collide with a
# built-in code (A1..E6) and STEP_ACTIONS.get() naturally returns no buttons.
CUSTOM_PREFIX = "custom:"

# Gap between built-in positions leaves room to insert custom steps between them.
_POSITION_GAP = 1000.0


def _builtin_position(code: str) -> float:
    """Default sort position for a built-in step (A1=1000 .. E6=22000)."""
    return (_BUILTIN[code][0] + 1) * _POSITION_GAP


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class CalendarEntry:
    """One thing to draw on a day in the month calendar."""
    kind: str                    # "step" | "etd"
    shipment_id: str
    label: str                   # "AMJ24"
    step_code: str | None
    title: str
    date: str                    # ISO yyyy-mm-dd
    is_complete: bool
    etd_source: str | None = None  # "bnct" | "manual" | "si" for an ETD entry

    def is_overdue(self, today: str) -> bool:
        """Unfinished and already past. Completion beats overdue, and a step
        dated today is not yet overdue.
        """
        return self.kind == "step" and not self.is_complete and self.date < today


@dataclass
class StepState:
    code: str
    status: str
    completed_at: str | None
    completion_source: str | None
    position: float = 0.0
    is_custom: bool = False
    title: str = ""
    description: str = ""
    remark: str | None = None
    remark_author: str | None = None
    remark_at: str | None = None
    added_by: str | None = None
    added_at: str | None = None
    due_date: str | None = None
    display_number: int = 0
    phase2_only: bool = False

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"

    @property
    def countable(self) -> bool:
        """Counts toward progress and the completion gate."""
        return self.is_custom or not self.phase2_only


def _label(row_or_values) -> str:
    """Short shipment label, e.g. "AMJ24" — kept in the audit trail so an entry
    still reads correctly after the shipment is deleted.
    """
    try:
        return (f"{row_or_values['exporter_code']}"
                f"{row_or_values['sequence_number']}")
    except (KeyError, TypeError):
        return ""


class Shipments:
    def __init__(self, db: Database):
        self.db = db
        self.audit = AuditLog(db)

    # -- reads ----------------------------------------------------------

    def active(self) -> list:
        return self.db.query(
            "SELECT * FROM shipments WHERE status='active' "
            "ORDER BY etd_belawan IS NULL, etd_belawan, created_at")

    def completed(self) -> list:
        return self.db.query(
            "SELECT * FROM shipments WHERE status='completed' "
            "ORDER BY completed_at DESC")

    def get(self, shipment_id: str):
        return self.db.query_one("SELECT * FROM shipments WHERE id=?", (shipment_id,))

    def all_keys(self) -> set[tuple[str, int]]:
        """Every shipment's (exporter_code, sequence_number), any status. The scan
        uses this to guarantee it never creates a second row for one shipment.
        """
        return {(r["exporter_code"], r["sequence_number"])
                for r in self.db.query(
                    "SELECT exporter_code, sequence_number FROM shipments")}

    def for_voyage(self, vessel_name: str | None, voyage: str | None) -> list:
        """Active shipments whose vessel + voyage match — a display-time join for
        the Monitor Kapal board (no stored FK, so several exporters can share one
        voyage and the link stays live). Uses the same fuzzy matchers as BNCT.
        """
        from .bnct import _name_matches, _voyage_matches

        if not vessel_name or not voyage:
            return []
        rows = self.db.query(
            "SELECT id, exporter_code, sequence_number, vessel_name, voyage "
            "FROM shipments WHERE status='active' "
            "AND vessel_name IS NOT NULL AND voyage IS NOT NULL")
        return [r for r in rows
                if _name_matches(vessel_name, r["vessel_name"] or "")
                and _voyage_matches(voyage, r["voyage"] or "")]

    def steps(self, shipment_id: str) -> list[StepState]:
        """Ordered steps for a shipment: the built-in workflow plus any custom
        steps, numbered 1..N. Pure read — no writes (it is called per active
        shipment by count_overdue_steps).
        """
        rows = self.db.query(
            "SELECT * FROM workflow_steps WHERE shipment_id=?", (shipment_id,))
        by_code = {r["step_code"]: r for r in rows}

        result: list[StepState] = []

        # Built-in steps: use the stored row if present, else synthesize a
        # pending one so a shipment created before a step existed still shows
        # the full checklist. Title/description come from the constants.
        for code, (_ordinal, title, description, phase2_only) in _BUILTIN.items():
            row = by_code.get(code)
            position = (row["position"] if row and row["position"] is not None
                        else _builtin_position(code))
            result.append(StepState(
                code=code,
                status=row["status"] if row else "pending",
                completed_at=row["completed_at"] if row else None,
                completion_source=row["completion_source"] if row else None,
                position=position, is_custom=False,
                title=title, description=description, phase2_only=phase2_only,
                remark=row["remark"] if row else None,
                remark_author=row["remark_author"] if row else None,
                remark_at=row["remark_at"] if row else None,
                due_date=row["due_date"] if row else None,
            ))

        # Custom steps: any stored row flagged custom whose code is not built-in.
        for code, row in by_code.items():
            if code in _BUILTIN or not row["is_custom"]:
                continue
            result.append(StepState(
                code=code, status=row["status"],
                completed_at=row["completed_at"],
                completion_source=row["completion_source"],
                position=(row["position"] if row["position"] is not None
                          else 99_000.0),
                is_custom=True,
                title=row["title"] or "(tanpa judul)", description="",
                remark=row["remark"], remark_author=row["remark_author"],
                remark_at=row["remark_at"], due_date=row["due_date"],
                added_by=row["added_by"], added_at=row["added_at"],
            ))

        result.sort(key=lambda s: (s.position, s.added_at or "", s.code))
        for i, step in enumerate(result, start=1):
            step.display_number = i
        return result

    def progress(self, shipment_id: str) -> tuple[int, int]:
        """(completed, total) over every countable step — built-in and custom."""
        countable = [s for s in self.steps(shipment_id) if s.countable]
        done = sum(1 for s in countable if s.is_complete)
        return done, len(countable)

    def all_steps_complete(self, shipment_id: str) -> bool:
        """True when every countable step is done — the completion gate."""
        countable = [s for s in self.steps(shipment_id) if s.countable]
        return bool(countable) and all(s.is_complete for s in countable)

    def count_completed_this_month(self) -> int:
        prefix = datetime.now().strftime("%Y-%m")
        row = self.db.query_one(
            "SELECT COUNT(*) AS c FROM shipments "
            "WHERE status='completed' AND completed_at LIKE ?", (f"{prefix}%",))
        return row["c"] if row else 0

    def count_overdue_steps(self) -> int:
        """PRD 4.1 — incomplete steps on shipments whose ETD has passed."""
        today = date.today().isoformat()
        total = 0
        for shipment in self.active():
            etd = shipment["etd_belawan"]
            if not etd or etd >= today:
                continue
            done, available = self.progress(shipment["id"])
            total += available - done
        return total

    # -- writes ---------------------------------------------------------

    def create(self, values: dict[str, Any], *, seed_steps: bool = True) -> str:
        shipment_id = values.get("id") or new_id()
        self.db.execute(
            "INSERT INTO shipments ("
            "id, exporter_code, sequence_number, booking_number, vessel_name, "
            "voyage, etd_belawan, etd_source, destination_port, "
            "destination_country, container_quantity, container_size_short, "
            "empty_pickup_location, quarantine_required, folder_path, "
            "do_pdf_filename, shipping_company, status, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?)",
            (
                shipment_id,
                values["exporter_code"],
                values["sequence_number"],
                values.get("booking_number"),
                values.get("vessel_name"),
                values.get("voyage"),
                values.get("etd_belawan"),
                values.get("etd_source"),
                values.get("destination_port"),
                values.get("destination_country"),
                values.get("container_quantity"),
                values.get("container_size_short"),
                values.get("empty_pickup_location"),
                1 if values.get("quarantine_required") else 0,
                values.get("folder_path"),
                values.get("do_pdf_filename"),
                values.get("shipping_company"),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        if seed_steps:
            self._seed_steps(shipment_id)
        vessel = f"{values.get('vessel_name') or ''} " \
                 f"{values.get('voyage') or ''}".strip()
        self.audit.record(CREATED, shipment_id=shipment_id,
                          label=_label(values), detail=vessel)
        return shipment_id

    def _seed_steps(self, shipment_id: str) -> None:
        with self.db.cursor(write=True) as cur:
            for code in _BUILTIN:
                cur.execute(
                    "INSERT OR IGNORE INTO workflow_steps "
                    "(id, shipment_id, step_code, status, position, is_custom) "
                    "VALUES (?,?,?,'pending',?,0)",
                    (new_id(), shipment_id, code, _builtin_position(code)),
                )

    def _ensure_builtin_row(self, shipment_id: str, step_code: str) -> None:
        """A shipment created before a step existed has no row for it, and
        set_step is UPDATE-only — insert the built-in row first so ticking it
        persists instead of silently no-op'ing.
        """
        if step_code in _BUILTIN:
            self.db.execute(
                "INSERT OR IGNORE INTO workflow_steps "
                "(id, shipment_id, step_code, status, position, is_custom) "
                "VALUES (?,?,?,'pending',?,0)",
                (new_id(), shipment_id, step_code, _builtin_position(step_code)))

    def set_step(self, shipment_id: str, step_code: str, complete: bool,
                 source: str = "manual") -> None:
        self._ensure_builtin_row(shipment_id, step_code)
        if complete:
            self.db.execute(
                "UPDATE workflow_steps SET status='complete', completed_at=?, "
                "completion_source=? WHERE shipment_id=? AND step_code=?",
                (_now(), source, shipment_id, step_code))
        else:
            # PRD 6.2 — unchecking is always silent and has no side effects.
            self.db.execute(
                "UPDATE workflow_steps SET status='pending', completed_at=NULL, "
                "completion_source=NULL WHERE shipment_id=? AND step_code=?",
                (shipment_id, step_code))

    # -- custom steps and remarks --------------------------------------

    @staticmethod
    def _position_for(ordered: list[StepState], anchor_code: str,
                      side: str) -> float:
        """Midpoint position to place a step before/after the anchor step."""
        index = next((i for i, s in enumerate(ordered)
                      if s.code == anchor_code), None)
        if index is None:                       # anchor gone — append at the end
            last = ordered[-1].position if ordered else 0.0
            return last + _POSITION_GAP
        anchor = ordered[index].position
        if side == "before":
            prev = ordered[index - 1].position if index > 0 else anchor - _POSITION_GAP
            return (prev + anchor) / 2
        nxt = (ordered[index + 1].position if index + 1 < len(ordered)
               else anchor + _POSITION_GAP)
        return (anchor + nxt) / 2

    def add_custom_step(self, shipment_id: str, title: str, *, author: str | None,
                        anchor_code: str | None = None,
                        side: str = "after") -> str:
        """Add a custom TODO step. Positioned relative to anchor_code, or at the
        end when anchor_code is None. Returns the new step code.
        """
        ordered = self.steps(shipment_id)
        if anchor_code:
            position = self._position_for(ordered, anchor_code, side)
        else:
            last = ordered[-1].position if ordered else 0.0
            position = last + _POSITION_GAP
        code = f"{CUSTOM_PREFIX}{new_id()}"
        self.db.execute(
            "INSERT INTO workflow_steps (id, shipment_id, step_code, status, "
            "position, is_custom, title, added_by, added_at) "
            "VALUES (?,?,?,'pending',?,1,?,?,?)",
            (new_id(), shipment_id, code, position, title.strip(),
             author or None, _now()))
        return code

    def delete_step(self, shipment_id: str, step_code: str) -> bool:
        """Delete a custom step. Built-in steps are guarded and never deleted."""
        if not step_code.startswith(CUSTOM_PREFIX):
            return False
        self.db.execute(
            "DELETE FROM workflow_steps WHERE shipment_id=? AND step_code=? "
            "AND is_custom=1", (shipment_id, step_code))
        return True

    def move_step(self, shipment_id: str, step_code: str, *, anchor_code: str,
                  side: str = "after") -> bool:
        """Reposition a custom step relative to another step. Custom only."""
        if not step_code.startswith(CUSTOM_PREFIX):
            return False
        ordered = [s for s in self.steps(shipment_id) if s.code != step_code]
        position = self._position_for(ordered, anchor_code, side)
        self.db.execute(
            "UPDATE workflow_steps SET position=? WHERE shipment_id=? "
            "AND step_code=? AND is_custom=1",
            (position, shipment_id, step_code))
        return True

    def set_step_remark(self, shipment_id: str, step_code: str, remark: str, *,
                        author: str | None) -> None:
        """Set or clear a step's single remark. Blank clears it."""
        self._ensure_builtin_row(shipment_id, step_code)
        text = (remark or "").strip()
        if text:
            self.db.execute(
                "UPDATE workflow_steps SET remark=?, remark_author=?, remark_at=? "
                "WHERE shipment_id=? AND step_code=?",
                (text, author or None, _now(), shipment_id, step_code))
        else:
            self.db.execute(
                "UPDATE workflow_steps SET remark=NULL, remark_author=NULL, "
                "remark_at=NULL WHERE shipment_id=? AND step_code=?",
                (shipment_id, step_code))

    def set_step_date(self, shipment_id: str, step_code: str,
                      iso: str | None) -> None:
        """Set or clear a step's date. Blank/None clears it."""
        self._ensure_builtin_row(shipment_id, step_code)
        value = (iso or "").strip() or None
        self.db.execute(
            "UPDATE workflow_steps SET due_date=? WHERE shipment_id=? "
            "AND step_code=?", (value, shipment_id, step_code))

    # -- calendar ----------------------------------------------------------

    def calendar_entries(self, start_iso: str,
                         end_iso: str) -> list[CalendarEntry]:
        """Dated action items plus shipment ETDs falling in [start, end], across
        ALL shipments (active and completed).
        """
        entries: list[CalendarEntry] = []

        rows = self.db.query(
            "SELECT a.id, a.shipment_id, a.due_date, a.status, a.title, "
            "       s.exporter_code, s.sequence_number "
            "FROM action_items a JOIN shipments s ON s.id = a.shipment_id "
            "WHERE a.due_date IS NOT NULL AND a.due_date BETWEEN ? AND ? "
            "ORDER BY a.due_date, s.exporter_code, s.sequence_number",
            (start_iso, end_iso))
        for row in rows:
            entries.append(CalendarEntry(
                kind="step", shipment_id=row["shipment_id"],
                label=f"{row['exporter_code']}{row['sequence_number']}",
                step_code=row["id"], title=row["title"] or "(tanpa judul)",
                date=row["due_date"][:10],
                is_complete=row["status"] == ACTION_STATUS_DONE))

        etds = self.db.query(
            "SELECT id, exporter_code, sequence_number, etd_belawan, etd_source, "
            "       vessel_name, voyage FROM shipments "
            "WHERE etd_belawan IS NOT NULL AND etd_belawan BETWEEN ? AND ? "
            "ORDER BY etd_belawan, exporter_code, sequence_number",
            (start_iso, end_iso))
        for row in etds:
            vessel = f"{row['vessel_name'] or ''} {row['voyage'] or ''}".strip()
            entries.append(CalendarEntry(
                kind="etd", shipment_id=row["id"],
                label=f"{row['exporter_code']}{row['sequence_number']}",
                step_code=None, title=f"ETD {vessel}".strip(),
                date=row["etd_belawan"][:10], is_complete=False,
                etd_source=row["etd_source"]))
        return entries

    def set_notes(self, shipment_id: str, notes: str | None) -> None:
        """Set or clear the shipment's free-text notes."""
        self.db.execute(
            "UPDATE shipments SET notes=? WHERE id=?",
            ((notes or "").strip() or None, shipment_id))

    def set_etd(self, shipment_id: str, iso: str | None) -> None:
        """Set the shipment's ETD (database only — never touches folder/Excel)."""
        self.db.execute(
            "UPDATE shipments SET etd_belawan=?, etd_source='manual' WHERE id=?",
            (iso or None, shipment_id))

    def set_vessel_voyage(self, shipment_id: str, vessel_name: str | None,
                          voyage: str | None) -> None:
        """Update a shipment's vessel + voyage (database only). Used when a
        re-scan finds the booking moved to a different vessel/voyage."""
        self.db.execute(
            "UPDATE shipments SET vessel_name=?, voyage=? WHERE id=?",
            (vessel_name or None, voyage or None, shipment_id))

    def set_voyage_etd(self, vessel_name: str | None, voyage: str | None,
                       iso: str | None) -> int:
        """Set the ETD for every active shipment on this vessel+voyage — a
        voyage is one departure, so its shipments share one ETD. Database only,
        no folder/Excel. Returns how many shipments were updated.
        """
        from .bnct import _name_matches, _voyage_matches

        if not vessel_name or not voyage:
            return 0
        rows = self.db.query(
            "SELECT id, vessel_name, voyage FROM shipments WHERE status='active' "
            "AND vessel_name IS NOT NULL AND voyage IS NOT NULL")
        ids = [r["id"] for r in rows
               if _name_matches(vessel_name, r["vessel_name"] or "")
               and _voyage_matches(voyage, r["voyage"] or "")]
        for shipment_id in ids:
            self.db.execute(
                "UPDATE shipments SET etd_belawan=?, etd_source='manual' "
                "WHERE id=?", (iso or None, shipment_id))
        return len(ids)

    def mark_complete(self, shipment_id: str) -> None:
        row = self.get(shipment_id)
        self.db.execute(
            "UPDATE shipments SET status='completed', completed_at=? WHERE id=?",
            (_now(), shipment_id))
        if row is not None:
            vessel = f"{row['vessel_name'] or ''} {row['voyage'] or ''}".strip()
            self.audit.record(COMPLETED, shipment_id=shipment_id,
                              label=_label(row), detail=vessel)

    def delete(self, shipment_id: str) -> None:
        # Read the label before the row goes, so the entry stays meaningful.
        row = self.get(shipment_id)
        label = _label(row) if row is not None else ""
        detail = (row["folder_path"] or "") if row is not None else ""
        self.db.execute("DELETE FROM shipments WHERE id=?", (shipment_id,))
        self.audit.record(DELETED, shipment_id=shipment_id, label=label,
                          detail=detail)
