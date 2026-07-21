"""In-app notification centre: store, view, sidebar badge, cascade (2026-07-21)."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from bot_kalung.core.context import AppContext
from bot_kalung.services.bnct import BnctReading, BnctVessel
from bot_kalung.services.bnct_monitor import BnctMonitor
from bot_kalung.services.notifications import NotificationStore
from bot_kalung.services.shipments import Shipments
from bot_kalung.ui.main_window import VIEW_NOTIFICATIONS, MainWindow

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


app = QApplication.instance() or QApplication([])

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)
    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set("setup_complete", "1")
    shipments = Shipments(ctx.db)
    store = NotificationStore(ctx.db)

    sid = shipments.create({
        "exporter_code": "NIT", "sequence_number": 15, "vessel_name": "MTT REYA",
        "voyage": "123N", "booking_number": "T22854", "etd_belawan": "2026-07-25",
        "destination_port": "CHENNAI", "destination_country": "INDIA",
        "container_quantity": 2, "container_size_short": "40'",
    })

    # ---- store basics ----------------------------------------------------
    check("no notifications to start", store.unread_count() == 0)
    n1 = store.add("schedule", sid, "NIT15: terjadwal", "ETD ...",
                   created_at="2026-07-21T09:00:00")
    store.add("alongside", sid, "NIT15: sandar", "Loading ...",
              created_at="2026-07-21T10:00:00")
    check("unread count reflects additions", store.unread_count() == 2)
    check("recent is newest-first",
          store.recent()[0].title == "NIT15: sandar")
    store.mark_read(n1)
    check("marking one read drops the count", store.unread_count() == 1)
    store.mark_all_read()
    check("mark all read clears the count", store.unread_count() == 0)

    # ---- monitor writes a notification on a transition -------------------
    monitor = BnctMonitor(ctx.db)
    reading = BnctReading(
        found=True, phase="alongside", checked_at="2026-07-21T11:00:00",
        vessel=BnctVessel("tpkb", "alongside", "MV. MTT REYA", "26RY123S",
                          "26RY123N", loading_plan=800, loading_remain=2))
    notes = monitor.process(sid, "NIT15", reading)
    check("monitor emitted transition notifications", len(notes) >= 1)
    check("monitor persisted them to the store",
          store.unread_count() == len(notes))
    check("a departing transition was recorded",
          any(n.kind == "departing" for n in store.recent()))

    # ---- MainWindow: badge, navigation, deep-link ------------------------
    window = MainWindow(ctx)
    unread = store.unread_count()
    check("sidebar badge shows the unread count",
          f"({unread})" in window.sidebar.notifications_button.text())

    window.open_notifications()
    check("notifications view is reachable",
          window.stack.currentIndex() == VIEW_NOTIFICATIONS)
    check("the view lists the notifications",
          window.notifications.store.unread_count() == unread)

    # Clicking a card marks it read and opens its shipment.
    first_card = window.notifications.list_layout.itemAt(0).widget()
    first_card.clicked.emit(first_card._note.id, sid)
    check("clicking a notification reduces the unread count",
          store.unread_count() == unread - 1)
    check("clicking a notification opens its shipment",
          window.current_shipment_id == sid)
    check("badge updates after reading one",
          f"({unread - 1})" in window.sidebar.notifications_button.text()
          or (unread - 1 == 0
              and "(" not in window.sidebar.notifications_button.text()))

    window.open_notifications()
    window.notifications._mark_all_read()
    check("mark all read empties the badge",
          "(" not in window.sidebar.notifications_button.text())

    # ---- cascade on shipment delete --------------------------------------
    store.add("schedule", sid, "another", "x", created_at="2026-07-21T12:00:00")
    check("a fresh notification exists before delete", store.unread_count() == 1)
    ctx.db.execute("DELETE FROM shipments WHERE id=?", (sid,))
    check("deleting the shipment cascades its notifications away",
          ctx.db.query_one("SELECT COUNT(*) c FROM notifications")["c"] == 0)

    window.wizard.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Notifications OK - all checks passed.")
