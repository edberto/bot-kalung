"""The Kelola Kapal management dialog (2026-08-07, offscreen).

Add creates a 3-voyage window; delete-voyage refills; delete-vessel clears all;
`changed` fires on every mutation.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

from bot_kalung.core.context import AppContext
from bot_kalung.services.vessel_monitor import MonitoredVessels, state_of
from bot_kalung.ui.vessel_manager_dialog import VesselManagerDialog

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
    store = MonitoredVessels(ctx.db)

    dialog = VesselManagerDialog(ctx.db)
    changes = []
    dialog.changed.connect(lambda: changes.append(1))

    def group(name):
        return store._group_rows(name)

    # ---- add requires both fields -----------------------------------------
    dialog.name_field.setText("WAN HAI 101")
    dialog.voyage_field.setText("")
    dialog._add()
    check("add is refused without a voyage", group("WAN HAI 101") == [])

    dialog.voyage_field.setText("N379")
    dialog._add()
    check("adding creates a 3-voyage window",
          sorted(r["voyage"] for r in group("WAN HAI 101"))
          == ["N379", "N380", "N381"])
    check("add fires 'changed'", changes and len(changes) == 1)
    check("the fields are cleared after add", dialog.name_field.text() == "")

    # ---- delete one voyage refills to 3 -----------------------------------
    victim = group("WAN HAI 101")[0]["id"]
    dialog._delete_voyage("WAN HAI 101", victim)
    check("a non-departed voyage removed then refilled stays at 3",
          sum(1 for r in group("WAN HAI 101") if state_of(r) != "departed") == 3)
    check("delete-voyage fires 'changed'", len(changes) == 2)

    # ---- delete the whole vessel ------------------------------------------
    store.add_vessel("INTEGRA", "182E")
    dialog.refresh()
    check("a second vessel shows in the dialog",
          set(store.groups()) == {"WAN HAI 101", "INTEGRA"})
    dialog._delete_vessel("WAN HAI 101")
    check("delete-vessel removes every voyage", group("WAN HAI 101") == [])
    check("the other vessel is untouched", len(group("INTEGRA")) == 3)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Vessel manager dialog OK - all checks passed.")
