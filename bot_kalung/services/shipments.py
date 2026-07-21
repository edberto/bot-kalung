"""Shipment and workflow-step persistence (PRD Sections 13.1 and 13.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ..core.constants import WORKFLOW_STEPS
from ..core.db import Database, new_id


@dataclass
class StepState:
    code: str
    status: str
    completed_at: str | None
    completion_source: str | None

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"


class Shipments:
    def __init__(self, db: Database):
        self.db = db

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

    def steps(self, shipment_id: str) -> list[StepState]:
        rows = self.db.query(
            "SELECT step_code, status, completed_at, completion_source "
            "FROM workflow_steps WHERE shipment_id=?", (shipment_id,))
        by_code = {r["step_code"]: r for r in rows}
        # Return every defined step, so a shipment created before a step was
        # added to the workflow still renders the full checklist.
        result = []
        for code, *_ in WORKFLOW_STEPS:
            row = by_code.get(code)
            result.append(StepState(
                code=code,
                status=row["status"] if row else "pending",
                completed_at=row["completed_at"] if row else None,
                completion_source=row["completion_source"] if row else None,
            ))
        return result

    def progress(self, shipment_id: str) -> tuple[int, int]:
        """(completed, total) counting only steps available in phase 1."""
        available = [s for s in WORKFLOW_STEPS if not s[5]]
        codes = {s[0] for s in available}
        done = sum(1 for s in self.steps(shipment_id)
                   if s.code in codes and s.is_complete)
        return done, len(available)

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

    def create(self, values: dict[str, Any]) -> str:
        shipment_id = values.get("id") or new_id()
        self.db.execute(
            "INSERT INTO shipments ("
            "id, exporter_code, sequence_number, booking_number, vessel_name, "
            "voyage, etd_belawan, destination_port, destination_country, "
            "container_quantity, container_size_short, empty_pickup_location, "
            "quarantine_required, folder_path, do_pdf_filename, "
            "shipping_company, status, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?)",
            (
                shipment_id,
                values["exporter_code"],
                values["sequence_number"],
                values.get("booking_number"),
                values.get("vessel_name"),
                values.get("voyage"),
                values.get("etd_belawan"),
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
        self._seed_steps(shipment_id)
        return shipment_id

    def _seed_steps(self, shipment_id: str) -> None:
        with self.db.cursor(write=True) as cur:
            for code, *_ in WORKFLOW_STEPS:
                cur.execute(
                    "INSERT OR IGNORE INTO workflow_steps "
                    "(id, shipment_id, step_code, status) VALUES (?,?,?,'pending')",
                    (new_id(), shipment_id, code),
                )

    def set_step(self, shipment_id: str, step_code: str, complete: bool,
                 source: str = "manual") -> None:
        if complete:
            self.db.execute(
                "UPDATE workflow_steps SET status='complete', completed_at=?, "
                "completion_source=? WHERE shipment_id=? AND step_code=?",
                (datetime.now().isoformat(timespec="seconds"), source,
                 shipment_id, step_code))
        else:
            # PRD 6.2 — unchecking is always silent and has no side effects.
            self.db.execute(
                "UPDATE workflow_steps SET status='pending', completed_at=NULL, "
                "completion_source=NULL WHERE shipment_id=? AND step_code=?",
                (shipment_id, step_code))

    def mark_complete(self, shipment_id: str) -> None:
        self.db.execute(
            "UPDATE shipments SET status='completed', completed_at=? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"), shipment_id))

    def delete(self, shipment_id: str) -> None:
        self.db.execute("DELETE FROM shipments WHERE id=?", (shipment_id,))
