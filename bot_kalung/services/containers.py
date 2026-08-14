"""Container tracking (folder-scan tracker, Phase 4).

Containers come off the VGM read at import; their live status is polled from the
BNCT container search. Each row keeps only the latest reading (last_*), which is
enough to detect the transition into 51-STACK RECEIVING without a history table.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.db import Database, new_id
from .bnct import STACK_RECEIVING_CODE


@dataclass
class ContainerRow:
    id: str
    shipment_id: str
    container_no: str
    size: str | None
    type: str | None
    last_site: str | None
    last_status_code: str | None
    last_status_text: str | None
    last_checked_at: str | None

    @property
    def status(self) -> str:
        if not self.last_status_code and not self.last_status_text:
            return "Belum ada data"
        return f"{self.last_status_code or ''}-{self.last_status_text or ''}".strip("-")

    @property
    def at_stack_receiving(self) -> bool:
        return self.last_status_code == STACK_RECEIVING_CODE


class Containers:
    def __init__(self, db: Database):
        self.db = db

    # -- populate (on import) -------------------------------------------

    def populate(self, shipment_id: str, container_numbers,
                 size: str | None = None) -> None:
        """Insert the shipment's containers. Idempotent on (shipment, number)."""
        with self.db.cursor(write=True) as cur:
            for number in container_numbers:
                number = (number or "").strip()
                if not number:
                    continue
                cur.execute(
                    "INSERT OR IGNORE INTO containers "
                    "(id, shipment_id, container_no, size) VALUES (?,?,?,?)",
                    (new_id(), shipment_id, number, size))

    # -- reads ----------------------------------------------------------

    def for_shipment(self, shipment_id: str) -> list[ContainerRow]:
        rows = self.db.query(
            "SELECT * FROM containers WHERE shipment_id=? ORDER BY container_no",
            (shipment_id,))
        return [self._row(r) for r in rows]

    def active_with_vessel(self) -> list:
        """Every container on an active shipment, with the shipment's vessel +
        voyage and label — the working set for the BNCT poll.
        """
        return self.db.query(
            "SELECT c.id, c.container_no, c.last_status_code, "
            "       s.vessel_name, s.voyage, s.exporter_code, s.sequence_number, "
            "       s.id AS shipment_id "
            "FROM containers c JOIN shipments s ON s.id = c.shipment_id "
            "WHERE s.status='active'")

    @staticmethod
    def _row(r) -> ContainerRow:
        return ContainerRow(
            id=r["id"], shipment_id=r["shipment_id"], container_no=r["container_no"],
            size=r["size"], type=r["type"], last_site=r["last_site"],
            last_status_code=r["last_status_code"],
            last_status_text=r["last_status_text"],
            last_checked_at=r["last_checked_at"])

    # -- poll write -----------------------------------------------------

    def update_status(self, container_id: str, *, site: str, status_code: str,
                      status_text: str, type: str | None,
                      checked_at: str) -> str | None:
        """Store the latest reading; return the PREVIOUS status code so the caller
        can spot a transition into 51-STACK RECEIVING.
        """
        row = self.db.query_one(
            "SELECT last_status_code FROM containers WHERE id=?", (container_id,))
        previous = row["last_status_code"] if row else None
        self.db.execute(
            "UPDATE containers SET last_site=?, last_status_code=?, "
            "last_status_text=?, type=COALESCE(?, type), last_checked_at=? "
            "WHERE id=?",
            (site, status_code, status_text, type, checked_at, container_id))
        return previous
