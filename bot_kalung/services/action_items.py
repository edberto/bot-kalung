"""Per-shipment action items (folder-scan tracker).

Replaces the fixed A1..E6 workflow_steps: each scanned shipment gets a short list
of documents/tasks, seeded from its destination and exporter, and every item
carries a status (pending → … → final) instead of a done flag. Ad-hoc items can
be added and any item can be deleted (lifecycle is manual, per the user).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from ..core.constants import (
    ACTION_ITEM_SEEDS,
    ACTION_STATUSES_DONE,
    ACTION_STATUSES,
    DG_EXPORTERS,
    INSURANCE_EXPORTERS,
    NO_BL_EXPORTERS,
    NO_COO_DESTINATIONS,
    NO_FUMI_EXPORTERS,
    NO_PHYTO_DESTINATIONS,
    NO_PHYTO_EXPORTERS,
)
from ..core.db import Database, new_id

CUSTOM_PREFIX = "custom:"
_POSITION_GAP = 1000.0


def _now() -> str:
    # UTC, like the PWA and Drive, so doc_status can compare a file's upload
    # time against the last status change.
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def should_seed(code: str, country: str | None, exporter_code: str | None,
                port: str | None = None) -> bool:
    """Whether a seed item applies to a shipment (rules in constants). The
    destination is checked against the port as well as the country, since a
    transit cargo reads "Tajikistan Via Karachi" with country Pakistan. An
    unknown destination excludes nothing."""
    dest = f"{port or ''} {country or ''}".upper()
    ex = (exporter_code or "").strip().upper()
    if code == "fumi":
        return ex not in NO_FUMI_EXPORTERS
    if code == "phyto":
        return ex not in NO_PHYTO_EXPORTERS and not any(
            d in dest for d in NO_PHYTO_DESTINATIONS)
    if code == "coo":
        return not any(d in dest for d in NO_COO_DESTINATIONS)
    if code == "bl":
        return ex not in NO_BL_EXPORTERS
    if code == "dg":
        return ex in DG_EXPORTERS
    if code == "insurance":
        return ex in INSURANCE_EXPORTERS
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
        return self.status in ACTION_STATUSES_DONE


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
        done = sum(1 for r in rows if r["status"] in ACTION_STATUSES_DONE)
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
            if r["status"] in ACTION_STATUSES_DONE:
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
            "AND a.due_date < ? AND a.status NOT IN (?, ?)", (today, *ACTION_STATUSES_DONE))
        return len(rows)

    # -- seeding on import ----------------------------------------------

    def seed(self, shipment_id: str, exporter_code: str | None,
             destination_country: str | None,
             destination_port: str | None = None) -> list[str]:
        """Add the applicable seed items the shipment doesn't have yet, in
        template order. Safe to re-run (existing codes are skipped). Returns the
        codes added."""
        have = {r["code"] for r in self.db.query(
            "SELECT code FROM action_items WHERE shipment_id=?", (shipment_id,))}
        added = []
        with self.db.cursor(write=True) as cur:
            for index, (code, title) in enumerate(ACTION_ITEM_SEEDS):
                if code in have or not should_seed(
                        code, destination_country, exporter_code, destination_port):
                    continue
                cur.execute(
                    "INSERT INTO action_items "
                    "(id, shipment_id, code, title, status, position, is_custom, "
                    " created_at) VALUES (?,?,?,?,'pending',?,0,?)",
                    (new_id(), shipment_id, code, title,
                     (index + 1) * _POSITION_GAP, _now()))
                added.append(code)
        return added

    # -- writes ---------------------------------------------------------

    def set_status(self, item_id: str, status: str) -> None:
        if status not in ACTION_STATUSES:
            raise ValueError(f"unknown action status: {status}")
        self.db.execute(
            "UPDATE action_items SET status=?, updated_at=? WHERE id=?",
            (status, _now(), item_id))

    # -- automatic status (doc_status) -----------------------------------

    def auto_rows(self, shipment_id: str) -> list:
        """Built-in items with their last automatic inference."""
        return self.db.query(
            "SELECT id, code, title, status, auto_status, auto_evidence, "
            "auto_evidence_ref, updated_at, created_at FROM action_items "
            "WHERE shipment_id=? AND is_custom=0",
            (shipment_id,))

    def ensure_item(self, shipment_id: str, code: str, title: str) -> bool:
        """Add a built-in item (at the end) if the shipment lacks it. True if added."""
        if self.db.query_one("SELECT 1 FROM action_items WHERE shipment_id=? AND code=?",
                             (shipment_id, code)):
            return False
        top = self.db.query_one(
            "SELECT MAX(position) AS p FROM action_items WHERE shipment_id=?",
            (shipment_id,))
        position = ((top["p"] if top else None) or 0.0) + _POSITION_GAP
        self.db.execute(
            "INSERT INTO action_items (id, shipment_id, code, title, status, position, "
            "is_custom, created_at) VALUES (?,?,?,?, 'pending', ?, 0, ?)",
            (new_id(), shipment_id, code, title, position, _now()))
        return True

    def record_auto(self, item_id: str, status: str | None, auto_status: str,
                    evidence: str | None, evidence_ref: str | None,
                    detail: str | None = None) -> None:
        """Store the bot's inference (and the file behind it); `status` is the new
        status to apply, or None to leave the current one (e.g. a manual edit)."""
        if status is not None:
            self.db.execute(
                "UPDATE action_items SET status=?, auto_status=?, auto_evidence=?, "
                "auto_evidence_ref=?, auto_detail=?, updated_at=? WHERE id=?",
                (status, auto_status, evidence, evidence_ref, detail, _now(), item_id))
        else:
            self.db.execute(
                "UPDATE action_items SET auto_status=?, auto_evidence=?, "
                "auto_evidence_ref=?, auto_detail=? WHERE id=?",
                (auto_status, evidence, evidence_ref, detail, item_id))

    def alert_overdue_revisions(self, notifications, hours: int = 6) -> int:
        """One notification per document whose revision (Draf direvisi) has been
        going on for more than `hours` — once per revision round: a new status
        change starts a new round (alerted_at < updated_at). Returns the count."""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None)
                  - timedelta(hours=hours)).isoformat(timespec="seconds")
        rows = self.db.query(
            "SELECT a.id, a.title, a.shipment_id, a.updated_at, s.exporter_code, "
            "s.sequence_number FROM action_items a JOIN shipments s ON s.id = a.shipment_id "
            "WHERE s.status='active' AND a.status='draft_revision' AND a.updated_at <= ? "
            "AND (a.alerted_at IS NULL OR a.alerted_at < a.updated_at)", (cutoff,))
        now = _now()
        for r in rows:
            notifications.add(
                "revision", r["shipment_id"],
                f"{r['exporter_code']}{r['sequence_number']}: revisi {r['title']} > {hours} jam",
                f"Direvisi sejak {r['updated_at'].replace('T', ' ')} UTC — belum ada draft baru.",
                created_at=now)
            self.db.execute("UPDATE action_items SET alerted_at=? WHERE id=?", (now, r["id"]))
        return len(rows)

    def set_checks(self, shipment_id: str, code: str, checks: list) -> bool:
        """Store an item's checklist (JSON). Written only when it changed — each
        write is a realtime event in every open PWA. True if it changed."""
        import json

        row = self.db.query_one(
            "SELECT id, checks FROM action_items WHERE shipment_id=? AND code=?",
            (shipment_id, code))
        if row is None:
            return False
        value = json.dumps(checks, ensure_ascii=False)
        if row["checks"] == value:
            return False
        self.db.execute("UPDATE action_items SET checks=? WHERE id=?", (value, row["id"]))
        return True

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
