"""Shipment lifecycle audit trail (2026-07-21)."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from bot_kalung.core.context import AppContext
from bot_kalung.services.audit import COMPLETED, CREATED, DELETED, AuditLog
from bot_kalung.services.shipments import Shipments
from bot_kalung.ui.main_window import VIEW_AUDIT, MainWindow

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


app = QApplication.instance() or QApplication([])


def new_shipment(shipments, seq=24):
    return shipments.create({
        "exporter_code": "AMJ", "sequence_number": seq,
        "vessel_name": "EVER CONCERT", "voyage": "0798-087N",
        "booking_number": "B", "etd_belawan": "2026-08-01",
        "destination_port": "KARACHI", "destination_country": "PAKISTAN",
        "container_quantity": 1, "container_size_short": "40'",
        "folder_path": "/tmp/AMJ24",
    })


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)
    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set_many({"setup_complete": "1", "my_email": "achen@x.com"})
    shipments = Shipments(ctx.db)
    log = AuditLog(ctx.db)

    check("log starts empty", log.count() == 0)

    # ---- create ----------------------------------------------------------
    sid = new_shipment(shipments)
    entries = log.recent()
    check("creating a shipment is recorded", len(entries) == 1
          and entries[0].action == CREATED)
    check("entry carries the shipment label", entries[0].label == "AMJ24")
    check("entry names the acting worker", entries[0].actor == "achen@x.com")
    check("entry keeps a readable detail",
          "EVER CONCERT" in entries[0].detail)
    check("entry is labelled in Indonesian",
          entries[0].action_label == "Pengiriman dibuat")

    # ---- complete --------------------------------------------------------
    shipments.mark_complete(sid)
    check("completing a shipment is recorded",
          log.recent()[0].action == COMPLETED)

    # ---- delete: the entry must OUTLIVE the shipment ---------------------
    shipments.delete(sid)
    after = log.recent()
    check("deleting a shipment is recorded", after[0].action == DELETED)
    check("the delete entry survives the shipment it describes",
          log.count() == 3 and shipments.get(sid) is None)
    check("the deleted shipment is still identifiable by label",
          after[0].label == "AMJ24")
    check("no cascade wiped the earlier entries",
          {e.action for e in after} == {CREATED, COMPLETED, DELETED})

    # newest first
    check("entries are newest-first", after[0].action == DELETED
          and after[-1].action == CREATED)

    # ---- scope: only lifecycle, not step ticks ---------------------------
    sid2 = new_shipment(shipments, seq=25)
    before = log.count()
    shipments.set_step(sid2, "A2", True)
    shipments.set_step_remark(sid2, "A2", "catatan", author="achen@x.com")
    shipments.add_custom_step(sid2, "TODO", author="achen@x.com")
    check("step ticks and remarks are not audited (lifecycle only)",
          log.count() == before)

    # ---- an unknown worker still records -------------------------------
    ctx.settings.set("my_email", "")
    sid3 = new_shipment(shipments, seq=26)
    check("a blank my_email still records the event",
          log.recent()[0].shipment_id == sid3)
    check("unknown actor is stored as empty, not a crash",
          log.recent()[0].actor in (None, ""))
    ctx.settings.set("my_email", "achen@x.com")

    # ---- UI --------------------------------------------------------------
    window = MainWindow(ctx)
    check("sidebar offers the activity log",
          window.sidebar.audit_button.text() == "Log Aktivitas")
    window.open_audit()
    check("activity log is reachable",
          window.stack.currentIndex() == VIEW_AUDIT)
    check("the log renders a card per entry",
          window.audit.list_layout.count() - 1 == log.count())
    check("empty note hidden when there are entries",
          window.audit.empty_note.isHidden())

    # The log is passive: it must not inflate the notification badge.
    badge = window.sidebar.notifications_button.text()
    check("the activity log does not drive the unread badge",
          "(" not in badge)

    window.wizard.shutdown()

    # ---- empty state -----------------------------------------------------
    other = AppContext()
    other.create(Path(tmp) / "Drive2")
    empty_window_log = AuditLog(other.db)
    check("a fresh database has an empty log", empty_window_log.count() == 0)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Audit log OK - all checks passed.")
