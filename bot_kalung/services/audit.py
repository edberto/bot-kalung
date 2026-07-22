"""Shipment lifecycle audit trail (2026-07-21).

The database is shared by three workers over Google Drive, so "who created /
completed / deleted this shipment, and when" is worth keeping. Scope is
deliberately narrow — lifecycle only, not every checkbox tick — so the log stays
readable.

Entries live in their own table rather than in `notifications`, for two reasons:
a notification's `shipment_id` cascades on delete (which would erase the very
"shipment deleted" entry), and the notification list drives the unread badge,
which this passive log must not inflate.

The acting worker is resolved from the shared `my_email` setting, so callers do
not have to thread an actor through every signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..core.db import Database, new_id

# Action codes, with their Indonesian labels for the UI.
CREATED = "shipment_created"
COMPLETED = "shipment_completed"
DELETED = "shipment_deleted"

ACTION_LABELS = {
    CREATED: "Pengiriman dibuat",
    COMPLETED: "Pengiriman ditandai selesai",
    DELETED: "Pengiriman dihapus",
}

ACTION_COLORS = {
    CREATED: "#2563eb",
    COMPLETED: "#059669",
    DELETED: "#b91c1c",
}


@dataclass
class AuditEntry:
    id: str
    created_at: str
    actor: str | None
    action: str
    shipment_id: str | None
    label: str
    detail: str

    @property
    def action_label(self) -> str:
        return ACTION_LABELS.get(self.action, self.action)


class AuditLog:
    def __init__(self, db: Database):
        self.db = db

    def _actor(self) -> str | None:
        """The worker using this install, from the shared settings table."""
        from ..core.settings import Settings

        try:
            return (Settings(self.db).get("my_email") or "").strip() or None
        except Exception:      # noqa: BLE001 - auditing must never break a write
            return None

    def record(self, action: str, *, shipment_id: str | None = None,
               label: str = "", detail: str = "",
               actor: str | None = None) -> None:
        """Append an entry. Never raises — an audit failure must not abort the
        operation being audited.
        """
        try:
            self.db.execute(
                "INSERT INTO audit_log (id, created_at, actor, action, "
                "shipment_id, label, detail) VALUES (?,?,?,?,?,?,?)",
                (new_id(), datetime.now().isoformat(timespec="seconds"),
                 actor if actor is not None else self._actor(),
                 action, shipment_id, label, detail))
        except Exception:      # noqa: BLE001 - see docstring
            pass

    def recent(self, limit: int = 200) -> list[AuditEntry]:
        rows = self.db.query(
            "SELECT * FROM audit_log ORDER BY created_at DESC, rowid DESC "
            "LIMIT ?", (limit,))
        return [AuditEntry(
            id=r["id"], created_at=r["created_at"], actor=r["actor"],
            action=r["action"], shipment_id=r["shipment_id"],
            label=r["label"] or "", detail=r["detail"] or "") for r in rows]

    def count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) c FROM audit_log")
        return row["c"] if row else 0
