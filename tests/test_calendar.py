"""Month calendar, per-step dates, and entry colours (2026-07-22)."""

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel

from bot_kalung.core.context import AppContext
from bot_kalung.services.shipments import Shipments
from bot_kalung.ui.calendar_view import MAX_PER_DAY, EntryCard, MonthCalendar
from bot_kalung.ui.main_window import VIEW_DETAIL, MainWindow

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


app = QApplication.instance() or QApplication([])
TODAY = date.today()


def iso(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)
    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set_many({"setup_complete": "1", "my_email": "a@x.com"})
    shipments = Shipments(ctx.db)

    sid = shipments.create({
        "exporter_code": "AMJ", "sequence_number": 24,
        "vessel_name": "EVER CONCERT", "voyage": "087N", "booking_number": "B",
        "etd_belawan": iso(5), "destination_port": "KARACHI",
        "destination_country": "PAKISTAN", "container_quantity": 1,
        "container_size_short": "40'",
    })

    # ---- setting and clearing a step date --------------------------------
    shipments.set_step_date(sid, "A2", iso(2))
    a2 = next(s for s in shipments.steps(sid) if s.code == "A2")
    check("step date persists", a2.due_date == iso(2))
    shipments.set_step_date(sid, "A2", None)
    check("step date clears",
          next(s for s in shipments.steps(sid) if s.code == "A2").due_date is None)

    # A step an older shipment predates still persists a date (UPSERT path).
    ctx.db.execute("DELETE FROM workflow_steps WHERE shipment_id=? AND step_code='A3'",
                   (sid,))
    shipments.set_step_date(sid, "A3", iso(1))
    check("a date sticks on a step the shipment predates",
          next(s for s in shipments.steps(sid) if s.code == "A3").due_date == iso(1))

    # ---- calendar_entries -------------------------------------------------
    shipments.set_step_date(sid, "A2", iso(-3))        # overdue
    shipments.set_step_date(sid, "A4", iso(-1))        # done + past
    shipments.set_step(sid, "A4", True)
    custom = shipments.add_custom_step(sid, "Cek dokumen", author="a@x.com")
    shipments.set_step_date(sid, custom, iso(2))

    entries = shipments.calendar_entries(iso(-30), iso(30))
    kinds = {e.kind for e in entries}
    check("entries carry both steps and the ETD", kinds == {"step", "etd"})
    check("built-in step titles are resolved from the constants",
          any(e.title == "Terima DO" or e.title.startswith("Beritahu") or
              e.kind == "step" for e in entries))
    check("a custom step's own title is used",
          any(e.title == "Cek dokumen" for e in entries))
    check("entries carry the shipment label",
          all(e.label == "AMJ24" for e in entries))
    check("out-of-range dates are excluded",
          shipments.calendar_entries(iso(200), iso(230)) == [])

    # completed shipments still contribute (all-shipments scope)
    shipments.mark_complete(sid)
    check("a completed shipment still appears on the calendar",
          len(shipments.calendar_entries(iso(-30), iso(30))) == len(entries))

    # ---- colour precedence -------------------------------------------------
    by_code = {e.step_code: e for e in entries if e.kind == "step"}
    check("an unfinished past-dated step is overdue",
          by_code["A2"].is_overdue(TODAY.isoformat()))
    check("a done past-dated step is NOT overdue (completion wins)",
          not by_code["A4"].is_overdue(TODAY.isoformat()))
    check("a future-dated step is not overdue",
          not by_code[custom].is_overdue(TODAY.isoformat()))
    shipments.set_step_date(sid, "B1", TODAY.isoformat())
    todays = next(e for e in shipments.calendar_entries(iso(-30), iso(30))
                  if e.step_code == "B1")
    check("a step dated today is not yet overdue",
          not todays.is_overdue(TODAY.isoformat()))

    # ---- the widget --------------------------------------------------------
    window = MainWindow(ctx)
    cal = window.dashboard.calendar

    check("calendar opens on the current month", cal.showing_today())
    check("month title is in Indonesian", str(TODAY.year) in cal.title.text())
    check("today's cell is highlighted", cal.cells[TODAY.day].is_today)

    states = {}
    for day, cell in cal.cells.items():
        for card in cell.findChildren(EntryCard):
            states.setdefault(card.state, []).append(day)
    check(f"all four card states render {sorted(states)}",
          {"done", "overdue", "pending", "etd"} <= set(states))
    check("the overdue card sits on its own day",
          (TODAY + timedelta(days=-3)).day in states.get("overdue", []))
    check("the done card is green even though its date passed",
          (TODAY + timedelta(days=-1)).day in states.get("done", []))
    check("the ETD card is a distinct state",
          (TODAY + timedelta(days=5)).day in states.get("etd", []))

    # ticking the overdue step turns it green
    shipments.set_step(sid, "A2", True)
    cal.reload()
    after = {}
    for day, cell in cal.cells.items():
        for card in cell.findChildren(EntryCard):
            after.setdefault(day, []).append(card.state)
    check("ticking an overdue step turns its card green",
          "done" in after.get((TODAY + timedelta(days=-3)).day, []))

    # ---- navigation --------------------------------------------------------
    cal.shift(1)
    check("next month moves the cursor", not cal.showing_today())
    check("today is not marked on another month",
          not any(c.is_today for c in cal.cells.values()))
    cal.shift(-1)
    check("previous month returns", cal.showing_today())
    cal.shift(5)
    cal.go_to_today()
    check("'Hari ini' jumps back from an arbitrary month", cal.showing_today())
    check("'Hari ini' restores the highlight", cal.cells[TODAY.day].is_today)

    # ---- per-day cap -------------------------------------------------------
    spare = MonthCalendar()

    class _E:
        def __init__(self, i):
            self.kind, self.shipment_id, self.label = "step", "x", f"S{i}"
            self.step_code, self.title = f"c{i}", "t"
            self.date, self.is_complete = TODAY.isoformat(), False

        def is_overdue(self, today):
            return False

    spare.set_entries([_E(i) for i in range(MAX_PER_DAY + 2)])
    cell = spare.cells[TODAY.day]
    check("a busy day caps its cards",
          len(cell.findChildren(EntryCard)) == MAX_PER_DAY)
    check("the overflow is reported, not silently dropped",
          any("lainnya" in label.text() for label in cell.findChildren(QLabel)))

    # ---- a long entry must not widen its day cell --------------------------
    def cell_widths(title: str):
        widget = MonthCalendar()
        widget.resize(900, 560)

        class _One:
            kind, shipment_id, label = "step", "x", "AMJ24"
            step_code, is_complete = "c", False
            date = TODAY.isoformat()

            def __init__(self, t):
                self.title = t

            def is_overdue(self, today):
                return False

        widget.set_entries([_One(title)])
        widget.show()
        for _ in range(3):
            app.processEvents()
        return [c.width() for c in widget.cells.values()], widget

    short_widths, _ = cell_widths("OK")
    long_widths, long_cal = cell_widths(
        "Terima nomor kontainer dan segel dari Toni lalu masukkan ke Excel")
    check("day cells share one width",
          max(short_widths) - min(short_widths) <= 2)
    check("a long entry does not widen its cell",
          max(long_widths) <= max(short_widths) + 2)

    long_card = next(c for cell in long_cal.cells.values()
                     for c in cell.findChildren(EntryCard))
    check("the long entry is cropped to fit",
          long_card.label.text() != long_card.label.full_text()
          and long_card.label.text().endswith("…"))
    check("the full text survives on the tooltip",
          "Terima nomor kontainer" in long_card.toolTip())

    # ---- clicking an entry navigates and focuses ---------------------------
    window.open_dashboard()
    card = next(c for cell in cal.cells.values()
                for c in cell.findChildren(EntryCard) if c.state == "pending")
    clicked = []
    cal.entry_clicked.connect(lambda s, c: clicked.append((s, c)))
    card.clicked.emit(sid, custom)
    check("clicking an entry emits shipment and step",
          clicked and clicked[-1][0] == sid)
    window._on_calendar_entry(sid, custom)
    check("clicking navigates to the shipment",
          window.stack.currentIndex() == VIEW_DETAIL
          and window.current_shipment_id == sid)
    check("the focused step exists in the checklist",
          custom in window.detail.checklist.rows)

    # A card for a deleted shipment must not crash or navigate.
    window.open_dashboard()
    window._on_calendar_entry("does-not-exist", "A1")
    check("an entry for a missing shipment is ignored",
          window.stack.currentIndex() != VIEW_DETAIL)

    window.wizard.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Calendar OK - all checks passed.")
