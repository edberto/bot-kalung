"""Per-shipment action items (folder-scan tracker).

Replaces the fixed A1..E6 workflow_steps: each scanned shipment gets a short list
of documents/tasks, seeded from its destination and exporter, and every item
carries a status (pending → … → final) instead of a done flag. Ad-hoc items can
be added and any item can be deleted (lifecycle is manual, per the user).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from ..core.constants import (
    ACTION_ITEM_SEEDS,
    ACTION_STATUS_DONE,
    ACTION_STATUSES,
    DG_MARKING_CODES,
    FUMI_COUNTRIES,
    PHYTO_COO_COUNTRIES,
)
from ..core.db import Database, new_id

CUSTOM_PREFIX = "custom:"
_POSITION_GAP = 1000.0


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def should_seed(code: str, country: str | None, exporter_code: str | None) -> bool:
    """Whether a seed item applies to a shipment.

    Fumi is for Pakistan/Philippines/India; Phyto and COO for Pakistan/
    Philippines; Marking and DG for HOPSON (HAI) and JMI; everything else always.
    A country that could not be read leaves the country-gated items out — the
    worker adds them by hand if needed.
    """
    c = (country or "").strip().upper()
    ex = (exporter_code or "").strip().upper()
    if code == "fumi":
        return c in FUMI_COUNTRIES
    if code in ("phyto", "coo"):
        return c in PHYTO_COO_COUNTRIES
    if code in ("marking", "dg"):
        return ex in DG_MARKING_CODES
    return True


@dataclass
class ActionItem:
    id: str
    code: str
    title: str
    status: str
    position: float
    is_custom: bool
    due_date: str | None

    @property
    def is_done(self) -> bool:
        return self.status == ACTION_STATUS_DONE


class ActionItems:
    def __init__(self, db: Database):
        self.db = db

    # -- reads ----------------------------------------------------------

    def list(self, shipment_id: str) -> list[ActionItem]:
        rows = self.db.query(
            "SELECT * FROM action_items WHERE shipment_id=? "
            "ORDER BY position, created_at, code", (shipment_id,))
        return [ActionItem(
            id=r["id"], code=r["code"], title=r["title"], status=r["status"],
            position=r["position"] or 0.0, is_custom=bool(r["is_custom"]),
            due_date=r["due_date"]) for r in rows]

    def progress(self, shipment_id: str) -> tuple[int, int]:
        """(done, total) where done counts items at the final status."""
        rows = self.db.query(
            "SELECT status FROM action_items WHERE shipment_id=?", (shipment_id,))
        done = sum(1 for r in rows if r["status"] == ACTION_STATUS_DONE)
        return done, len(rows)

    def progress_map(self, shipment_ids) -> dict[str, tuple[int, int]]:
        """{shipment_id: (done, total)} for many shipments in ONE query.

        The sidebar shows a progress count per active shipment; querying each one
        separately opens a fresh connection per shipment to the Drive-hosted DB,
        which is slow. This batches them into a single round-trip.
        """
        ids = list(shipment_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self.db.query(
            f"SELECT shipment_id, status FROM action_items "
            f"WHERE shipment_id IN ({placeholders})", tuple(ids))
        tally: dict[str, list] = {sid: [0, 0] for sid in ids}
        for r in rows:
            entry = tally[r["shipment_id"]]
            entry[1] += 1
            if r["status"] == ACTION_STATUS_DONE:
                entry[0] += 1
        return {sid: (done, total) for sid, (done, total) in tally.items()}

    def count_overdue(self) -> int:
        """Action items on active shipments whose due date has passed and are not
        yet final — the dashboard's overdue metric.
        """
        today = date.today().isoformat()
        rows = self.db.query(
            "SELECT a.id FROM action_items a JOIN shipments s ON s.id=a.shipment_id "
            "WHERE s.status='active' AND a.due_date IS NOT NULL "
            "AND a.due_date < ? AND a.status != ?", (today, ACTION_STATUS_DONE))
        return len(rows)

    # -- seeding on import ----------------------------------------------

    def seed(self, shipment_id: str, exporter_code: str | None,
             destination_country: str | None) -> None:
        """Insert the applicable seed items in order. Idempotent per code."""
        with self.db.cursor(write=True) as cur:
            for index, (code, title) in enumerate(ACTION_ITEM_SEEDS):
                if not should_seed(code, destination_country, exporter_code):
                    continue
                cur.execute(
                    "INSERT INTO action_items "
                    "(id, shipment_id, code, title, status, position, is_custom, "
                    " created_at) VALUES (?,?,?,?,'pending',?,0,?)",
                    (new_id(), shipment_id, code, title,
                     (index + 1) * _POSITION_GAP, _now()))

    # -- writes ---------------------------------------------------------

    def set_status(self, item_id: str, status: str) -> None:
        if status not in ACTION_STATUSES:
            raise ValueError(f"unknown action status: {status}")
        self.db.execute(
            "UPDATE action_items SET status=?, updated_at=? WHERE id=?",
            (status, _now(), item_id))

    def set_due_date(self, item_id: str, iso: str | None) -> None:
        self.db.execute(
            "UPDATE action_items SET due_date=?, updated_at=? WHERE id=?",
            ((iso or "").strip() or None, _now(), item_id))

    def add_custom(self, shipment_id: str, title: str) -> str:
        items = self.list(shipment_id)
        position = (items[-1].position if items else 0.0) + _POSITION_GAP
        item_id = f"{CUSTOM_PREFIX}{new_id()}"
        self.db.execute(
            "INSERT INTO action_items "
            "(id, shipment_id, code, title, status, position, is_custom, created_at) "
            "VALUES (?,?,?,?,'pending',?,1,?)",
            (item_id, shipment_id, "custom", title.strip(), position, _now()))
        return item_id

    def delete(self, item_id: str) -> None:
        """Delete any action item — built-in or ad-hoc (lifecycle is manual)."""
        self.db.execute("DELETE FROM action_items WHERE id=?", (item_id,))
