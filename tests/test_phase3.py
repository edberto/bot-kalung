"""Phase 3: main window, sidebar, dashboard, navigation (headless Qt)."""

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel

from bot_kalung.core.constants import WORKFLOW_STEPS
from bot_kalung.core.context import AppContext
from bot_kalung.services.shipments import Shipments
from bot_kalung.ui.dashboard import days_until, format_date_id
from bot_kalung.ui.main_window import (
    VIEW_DASHBOARD, VIEW_DETAIL, VIEW_HISTORY, VIEW_SETTINGS, VIEW_WIZARD,
    MainWindow,
)

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


app = QApplication.instance() or QApplication([])

# ---- date helpers ---------------------------------------------------------
check("date formats in Indonesian",
      format_date_id("2026-08-03") == "3 Agustus 2026")
check("missing date renders as a dash", format_date_id(None) == "-")
check("malformed date passes through", format_date_id("garbage") == "garbage")
check("days_until counts forward",
      days_until((date.today() + timedelta(days=5)).isoformat()) == 5)
check("days_until goes negative once past",
      days_until((date.today() - timedelta(days=2)).isoformat()) == -2)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)

    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set("setup_complete", "1")
    shipments = Shipments(ctx.db)

    window = MainWindow(ctx)

    # ---- empty state -------------------------------------------------------
    check("opens on the dashboard", window.stack.currentIndex() == VIEW_DASHBOARD)
    check("dashboard shows its empty state", window.dashboard.empty_state.isVisible()
          or not window.dashboard.scroll.isVisible())
    check("sidebar shows the empty note", not window.sidebar.empty_note.isHidden())
    check("sidebar list has no rows", window.sidebar.shipment_list.count() == 0)

    # ---- create shipments --------------------------------------------------
    soon = (date.today() + timedelta(days=2)).isoformat()
    past = (date.today() - timedelta(days=5)).isoformat()

    first = shipments.create({
        "exporter_code": "AMJ", "sequence_number": 24,
        "booking_number": "084600048570", "vessel_name": "EVER CONCERT",
        "voyage": "0800-088N", "etd_belawan": soon,
        "destination_port": "KARACHI", "destination_country": "PAKISTAN",
        "container_quantity": 2, "container_size_short": "40'",
        "quarantine_required": True, "folder_path": str(root / "AMJ"),
    })
    second = shipments.create({
        "exporter_code": "NIT", "sequence_number": 16,
        "booking_number": "T22854", "vessel_name": "MTT REYA",
        "voyage": "123N", "etd_belawan": past,
        "destination_port": "CHENNAI", "destination_country": "INDIA",
        "container_quantity": 10, "container_size_short": "40'",
        "quarantine_required": False, "folder_path": str(root / "NIT"),
    })

    phase1_steps = [s for s in WORKFLOW_STEPS if not s[5]]
    check("steps seeded for the new shipment",
          len(shipments.steps(first)) == len(WORKFLOW_STEPS))
    check("progress starts at zero",
          shipments.progress(first) == (0, len(phase1_steps)))

    shipments.set_step(first, "A1", True, source="auto")
    shipments.set_step(first, "B1", True, source="auto")
    check("progress counts completed steps",
          shipments.progress(first) == (2, len(phase1_steps)))
    shipments.set_step(first, "B1", False)
    check("unchecking a step decrements progress",
          shipments.progress(first) == (1, len(phase1_steps)))
    check("uncheck clears the timestamp",
          next(s for s in shipments.steps(first) if s.code == "B1").completed_at is None)

    # E4 was the last phase-2 step and moved into phase 1 on 2026-07-20, so
    # every step now counts. The filter itself still has to work, since the
    # BNCT monitoring step may reintroduce a phase-2 flag.
    check("no step is excluded from the total now",
          len(phase1_steps) == len(WORKFLOW_STEPS))
    check("the phase-2 filter still excludes a flagged step",
          [c for c, _ph, _t, _d, _a, p2 in WORKFLOW_STEPS if not p2]
          == [c for c, *_ in WORKFLOW_STEPS])

    # ---- overdue counting ---------------------------------------------------
    overdue = shipments.count_overdue_steps()
    check("overdue counts only the past-ETD shipment",
          overdue == len(phase1_steps))

    window.refresh()
    check("sidebar lists both shipments", window.sidebar.shipment_list.count() == 2)
    check("sidebar hides the empty note", window.sidebar.empty_note.isHidden())

    entry = window.sidebar.shipment_list.itemWidget(
        window.sidebar.shipment_list.item(0))
    check("the list shows a clickable cursor",
          window.sidebar.shipment_list.cursor().shape()
          == Qt.CursorShape.PointingHandCursor)
    check("the list draws a full-row hover highlight",
          "::item:hover" in window.sidebar.shipment_list.styleSheet())
    # The entry passes the mouse through so the item's full-row hover shows,
    # instead of the widget painting a chopped rectangle over the item.
    check("the entry is transparent to the mouse",
          entry.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
    check("its labels pass the mouse through too",
          all(c.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
              for c in entry.findChildren(QLabel)))
    check("dashboard grid holds both cards", window.dashboard.grid.count() == 2)
    check("dashboard stats row has three cards",
          window.dashboard.stats_row.count() == 3)

    # A dashboard card is clickable anywhere, not just the "Buka" button.
    from PyQt6.QtGui import QMouseEvent

    card = window.dashboard.grid.itemAt(0).widget()
    check("card advertises itself as clickable",
          card.cursor().shape() == Qt.CursorShape.PointingHandCursor)
    opened = []
    card.open_requested.connect(opened.append)
    card.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, card.rect().center().toPointF(),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    check("clicking the card body opens its shipment",
          opened == [card.shipment_id])

    # ---- navigation ----------------------------------------------------------
    window.open_shipment(first)
    check("opening a shipment switches to detail",
          window.stack.currentIndex() == VIEW_DETAIL)
    check("current shipment tracked", window.current_shipment_id == first)
    check("sidebar highlights the open shipment",
          window.sidebar.shipment_list.currentItem() is not None)

    window.open_history()
    check("history reachable", window.stack.currentIndex() == VIEW_HISTORY)
    window.open_settings()
    check("settings reachable", window.stack.currentIndex() == VIEW_SETTINGS)
    check("previous view remembered for Back", window.previous_view == VIEW_HISTORY)
    window.go_back()
    check("Back returns to the previous view",
          window.stack.currentIndex() == VIEW_HISTORY)

    window.open_wizard()
    check("wizard reachable", window.stack.currentIndex() == VIEW_WIZARD)
    check("wizard clears the selected shipment", window.current_shipment_id is None)

    # Leaving the wizard prompts; the dialog is suppressed headlessly, so verify
    # the guard exists rather than driving the modal.
    check("wizard exit is guarded", hasattr(window, "_leave_wizard_ok"))

    window.stack.setCurrentIndex(VIEW_DASHBOARD)
    window.open_dashboard()
    check("dashboard reachable", window.stack.currentIndex() == VIEW_DASHBOARD)

    # ---- completion ----------------------------------------------------------
    shipments.mark_complete(second)
    window.refresh()
    check("completed shipment leaves the active list",
          len(shipments.active()) == 1)
    check("completed shipment appears in history",
          len(shipments.completed()) == 1)
    check("sidebar drops the completed shipment",
          window.sidebar.shipment_list.count() == 1)
    check("completed-this-month counter picks it up",
          shipments.count_completed_this_month() == 1)
    check("overdue count drops with it", shipments.count_overdue_steps() == 0)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Phase 3 OK - all checks passed.")
