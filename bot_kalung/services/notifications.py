"""In-app notification centre (2026-07-21).

Windows tray toasts vanish after a few seconds and the OS ignores any duration
the app asks for, so notifications are also persisted here: an unread counter on
the sidebar and a list the worker can revisit. Today the only source is BNCT
monitoring, but the store is source-agnostic.

Stored in the shared database, so the notification log and its read/unread
state are team-wide — everyone coordinating the same shipments sees the same
list. (If per-user state is ever wanted, this is where it would change.)
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.db import Database, new_id


@dataclass
class NotificationRow:
    id: str
    shipment_id: str | None
    kind: str
    title: str
    body: str
    created_at: str
    read: bool


class NotificationStore:
    def __init__(self, db: Database):
        self.db = db

    def add(self, kind: str, shipment_id: str | None, title: str, body: str,
            *, created_at: str) -> str:
        note_id = new_id()
        self.db.execute(
            "INSERT INTO notifications (id, shipment_id, kind, title, body, "
            "created_at, read) VALUES (?,?,?,?,?,?,0)",
            (note_id, shipment_id, kind, title, body, created_at))
        return note_id

    def unread_count(self) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) c FROM notifications WHERE read=0")
        return row["c"] if row else 0

    def recent(self, limit: int = 100) -> list[NotificationRow]:
        rows = self.db.query(
            "SELECT * FROM notifications ORDER BY created_at DESC, rowid DESC "
            "LIMIT ?", (limit,))
        return [self._row(r) for r in rows]

    def mark_read(self, note_id: str) -> None:
        self.db.execute("UPDATE notifications SET read=1 WHERE id=?", (note_id,))

    def mark_all_read(self) -> None:
        self.db.execute("UPDATE notifications SET read=1 WHERE read=0")

    def clear(self) -> None:
        self.db.execute("DELETE FROM notifications")

    @staticmethod
    def _row(r) -> NotificationRow:
        return NotificationRow(
            id=r["id"], shipment_id=r["shipment_id"], kind=r["kind"],
            title=r["title"], body=r["body"] or "", created_at=r["created_at"],
            read=bool(r["read"]))
