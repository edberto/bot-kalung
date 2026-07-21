"""Ad-hoc custom steps + per-step remarks + flat completion (2026-07-21)."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from bot_kalung.core.constants import STEP_CODES
from bot_kalung.core.context import AppContext
from bot_kalung.services.shipments import CUSTOM_PREFIX, Shipments
from bot_kalung.ui import shipment_detail
from bot_kalung.ui.main_window import MainWindow

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def codes(steps):
    return [s.code for s in steps]


app = QApplication.instance() or QApplication([])

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)
    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set("setup_complete", "1")
    shipments = Shipments(ctx.db)
    sid = shipments.create({
        "exporter_code": "AMJ", "sequence_number": 24, "vessel_name": "X",
        "voyage": "1N", "booking_number": "B", "etd_belawan": "2026-08-01",
        "destination_port": "KARACHI", "destination_country": "PAKISTAN",
        "container_quantity": 1, "container_size_short": "40'",
    })

    # ---- service layer ---------------------------------------------------
    base = shipments.steps(sid)
    check("all built-in steps present and numbered",
          codes(base) == STEP_CODES
          and [s.display_number for s in base] == list(range(1, 23)))

    # add after A1 -> lands between A1 and A2
    c1 = shipments.add_custom_step(sid, "Cek dokumen", author="a@x.com",
                                   anchor_code="A1", side="after")
    after = shipments.steps(sid)
    idx = {s.code: i for i, s in enumerate(after)}
    check("custom step lands right after its anchor",
          idx["A1"] + 1 == idx[c1] and idx[c1] + 1 == idx["A2"])
    cstep = next(s for s in after if s.code == c1)
    check("custom step is flagged, titled and attributed",
          cstep.is_custom and cstep.title == "Cek dokumen"
          and cstep.added_by == "a@x.com" and cstep.added_at)
    check("custom code never collides with a built-in",
          c1.startswith(CUSTOM_PREFIX) and c1 not in STEP_CODES)

    # add before A1 -> first
    c2 = shipments.add_custom_step(sid, "Langkah awal", author="a@x.com",
                                   anchor_code="A1", side="before")
    check("custom step can be placed before the first built-in",
          shipments.steps(sid)[0].code == c2)

    # add at the end (no anchor)
    c3 = shipments.add_custom_step(sid, "Langkah akhir", author="a@x.com")
    check("custom step with no anchor lands at the end",
          shipments.steps(sid)[-1].code == c3)

    # renumbering stays 1..N
    now = shipments.steps(sid)
    check("display numbers stay contiguous 1..N",
          [s.display_number for s in now] == list(range(1, len(now) + 1)))

    # custom step has no action buttons but a working checkbox
    shipments.set_step(sid, c1, True)
    check("custom step can be ticked", next(
        s for s in shipments.steps(sid) if s.code == c1).is_complete)

    # move: c3 (last) up to before A2
    shipments.move_step(sid, c3, anchor_code="A2", side="before")
    moved = shipments.steps(sid)
    check("custom step moves across built-ins",
          {s.code: i for i, s in enumerate(moved)}[c3]
          < {s.code: i for i, s in enumerate(moved)}["A2"])
    check("moving a built-in is refused",
          shipments.move_step(sid, "A2", anchor_code="A1", side="after") is False)

    # delete a custom; built-in delete is guarded
    check("deleting a custom step works", shipments.delete_step(sid, c2))
    check("deleting a built-in is refused", shipments.delete_step(sid, "A1") is False)
    check("A1 survives the guarded delete",
          any(s.code == "A1" for s in shipments.steps(sid)))

    # remark on a built-in and on a custom, then clear
    shipments.set_step_remark(sid, "A1", "menunggu Toni", author="a@x.com")
    a1 = next(s for s in shipments.steps(sid) if s.code == "A1")
    check("remark set on a built-in with author",
          a1.remark == "menunggu Toni" and a1.remark_author == "a@x.com")
    shipments.set_step_remark(sid, "A1", "  ", author="a@x.com")
    check("blank remark clears it",
          next(s for s in shipments.steps(sid) if s.code == "A1").remark is None)

    # ---- completion gate -------------------------------------------------
    check("not complete while a custom step is open",
          not shipments.all_steps_complete(sid))
    for s in shipments.steps(sid):
        shipments.set_step(sid, s.code, True)
    check("complete once every step (built-in + custom) is done",
          shipments.all_steps_complete(sid))
    shipments.set_step(sid, c3, False)
    check("an open custom step blocks completion again",
          not shipments.all_steps_complete(sid))
    done, total = shipments.progress(sid)
    check("progress counts custom steps in the total", total == len(shipments.steps(sid)))

    # ---- UI flow via MainWindow -----------------------------------------
    # Auto-confirm the delete dialog and stub the add dialog.
    from PyQt6.QtWidgets import QMessageBox
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)

    window = MainWindow(ctx)
    window.open_shipment(sid)
    detail = window.detail
    before_total = shipments.progress(sid)[1]

    class _FakeDialog:
        def __init__(self, steps, parent=None):
            pass

        def exec(self):
            from PyQt6.QtWidgets import QDialog
            return QDialog.DialogCode.Accepted

        def result_values(self):
            return ("Langkah UI", None, "after")

    shipment_detail._AddStepDialog = _FakeDialog
    detail._on_add_custom()
    check("adding a custom step through the UI grows the list",
          shipments.progress(sid)[1] == before_total + 1)
    new_code = shipments.steps(sid)[-1].code
    check("the UI-added step is custom and titled",
          new_code.startswith(CUSTOM_PREFIX)
          and next(s for s in shipments.steps(sid)
                   if s.code == new_code).title == "Langkah UI")

    # remark via the checklist signal path
    detail._on_remark_edited("B1", "catatan UI")
    check("remark added through the UI persists",
          next(s for s in shipments.steps(sid) if s.code == "B1").remark == "catatan UI")

    # delete via the UI (dialog auto-confirmed)
    detail._on_delete_custom(new_code)
    check("deleting a custom step through the UI removes it",
          not any(s.code == new_code for s in shipments.steps(sid)))

    # the checklist renders one flat list, no phase sections
    check("checklist has no phase sections", not hasattr(detail.checklist, "sections"))
    check("checklist row count matches the step count",
          len(detail.checklist.rows) == len(shipments.steps(sid)))

    window.wizard.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Custom steps OK - all checks passed.")
