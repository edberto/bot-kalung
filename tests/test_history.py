"""Shipment History: search, filters, sorting, Open Folder (PRD Section 12)."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from bot_kalung.core.context import AppContext
from bot_kalung.services.shipments import Shipments
from bot_kalung.ui.main_window import VIEW_HISTORY, MainWindow

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


app = QApplication.instance() or QApplication([])


def column(table, index: int) -> list[str]:
    return [table.item(r, index).text() for r in range(table.rowCount())]


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    root = tmp / "Drive"
    (root / "AMJ").mkdir(parents=True)
    real_folder = root / "AMJ" / "2026" / "20.2x40'-karachi"
    real_folder.mkdir(parents=True)

    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set("setup_complete", "1")
    shipments = Shipments(ctx.db)

    window = MainWindow(ctx)
    window.open_history()
    view = window.history

    check("history is reachable", window.stack.currentIndex() == VIEW_HISTORY)
    check("empty state shown with no completed shipments",
          not view.empty_state.isHidden())
    check("table hidden when empty", view.table.isHidden())

    # Three completed shipments plus one still active.
    data = [
        ("AMJ", 20, "084600048570", "EVER CONCERT", "0800-088N", "2026-08-03",
         "KARACHI", str(real_folder)),
        ("NIT", 15, "T22854", "MTT REYA", "123N", "2026-07-25",
         "CHENNAI", str(tmp / "missing")),
        ("TTJ", 4, "2331585571", "INTEGRA", "179E", "2025-11-02",
         "KARACHI", str(tmp / "gone")),
    ]
    ids = []
    for code, seq, booking, vessel, voyage, etd, dest, folder in data:
        sid = shipments.create({
            "exporter_code": code, "sequence_number": seq,
            "booking_number": booking, "vessel_name": vessel, "voyage": voyage,
            "etd_belawan": etd, "destination_port": dest,
            "destination_country": "PAKISTAN", "container_quantity": 2,
            "container_size_short": "40'", "folder_path": folder,
        })
        shipments.mark_complete(sid)
        ids.append(sid)

    active = shipments.create({
        "exporter_code": "AMJ", "sequence_number": 21,
        "booking_number": "999", "vessel_name": "ACTIVE ONE", "voyage": "1A",
        "etd_belawan": "2026-09-01", "destination_port": "KARACHI",
        "destination_country": "PAKISTAN", "container_quantity": 1,
        "container_size_short": "40'",
    })

    view.refresh()
    check("completed shipments listed", view.table.rowCount() == 3)
    check("active shipment excluded", "ACTIVE ONE" not in " ".join(column(view.table, 2)))
    check("count label reflects the total", "3" in view.count_label.text())
    check("table visible now", not view.table.isHidden())

    # ---- search ----------------------------------------------------------
    view.search.setText("chennai")
    check("search matches destination", view.table.rowCount() == 1
          and column(view.table, 4) == ["CHENNAI"])
    view.search.setText("2331585571")
    check("search matches booking number", view.table.rowCount() == 1)
    view.search.setText("integra")
    check("search matches vessel, case-insensitively", view.table.rowCount() == 1)
    view.search.setText("AMJ")
    check("search matches exporter code", view.table.rowCount() == 1)
    view.search.setText("nothing here")
    check("no matches shows a distinct message", view.table.rowCount() == 0
          and "cocok" in view.empty_state.text())
    view.search.setText("")
    check("clearing search restores all rows", view.table.rowCount() == 3)

    # ---- exporter chips ---------------------------------------------------
    amj_chip = next(c for c in view.chips if c.code == "AMJ")
    amj_chip.setChecked(True)
    check("exporter chip filters", view.table.rowCount() == 1
          and column(view.table, 0) == ["AMJ"])
    nit_chip = next(c for c in view.chips if c.code == "NIT")
    nit_chip.setChecked(True)
    check("two chips act as OR", view.table.rowCount() == 2)
    amj_chip.setChecked(False)
    nit_chip.setChecked(False)
    check("clearing chips restores all rows", view.table.rowCount() == 3)

    # ---- year filter ------------------------------------------------------
    check("year options built from the data", view.year_combo.count() == 3)
    index_2025 = view.year_combo.findData("2025")
    view.year_combo.setCurrentIndex(index_2025)
    check("year filter narrows to 2025", view.table.rowCount() == 1
          and column(view.table, 0) == ["TTJ"])
    view.year_combo.setCurrentIndex(0)
    check("'Semua' restores all rows", view.table.rowCount() == 3)

    # search and chips combine
    view.search.setText("karachi")
    amj_chip.setChecked(True)
    check("search and chip combine", view.table.rowCount() == 1
          and column(view.table, 0) == ["AMJ"])
    view.search.setText("")
    amj_chip.setChecked(False)

    # ---- sorting ----------------------------------------------------------
    view.table.sortItems(5, Qt.SortOrder.AscendingOrder)   # ETD
    check("ETD sorts chronologically, not as text",
          column(view.table, 0) == ["TTJ", "NIT", "AMJ"])
    view.table.sortItems(5, Qt.SortOrder.DescendingOrder)
    check("ETD sorts descending too",
          column(view.table, 0) == ["AMJ", "NIT", "TTJ"])
    view.table.sortItems(1, Qt.SortOrder.AscendingOrder)   # sequence
    check("sequence sorts numerically",
          column(view.table, 1) == ["4", "15", "20"])

    # ---- dates rendered in Indonesian --------------------------------------
    amj_row = next(r for r in range(view.table.rowCount())
                   if view.table.item(r, 0).text() == "AMJ")
    check("ETD shown in Indonesian",
          view.table.item(amj_row, 5).text() == "3 Agustus 2026")
    check("completion date shown", view.table.item(amj_row, 6).text() != "-")

    # ---- Open Folder --------------------------------------------------------
    opened = []
    view.open_folder_requested.connect(opened.append)

    view._open(str(real_folder))
    check("existing folder emits the open request", opened == [str(real_folder)])
    check("no error for an existing folder", view.message.isHidden())

    view._open(str(tmp / "missing"))
    check("missing folder does not emit", len(opened) == 1)
    check("missing folder reports an error", not view.message.isHidden()
          and "dipindahkan" in view.message.text())

    view._open("")
    check("blank folder path reports an error", not view.message.isHidden())

    # ---- delete from history ------------------------------------------------
    from PyQt6.QtWidgets import QMessageBox

    from bot_kalung.services.fileops import FolderDeleteResult
    from bot_kalung.ui import history as history_module

    view.search.setText("")
    for chip in view.chips:
        chip.setChecked(False)
    view.year_combo.setCurrentIndex(0)
    view.refresh()
    check("all three rows present before deletion", view.table.rowCount() == 3)

    trashed = []
    history_module.fileops.delete_shipment_folder = lambda f: (
        trashed.append(f)
        or FolderDeleteResult(True, True, bool(f), "Folder dipindahkan ke Recycle Bin."))
    QMessageBox.exec = lambda self: QMessageBox.StandardButton.Yes

    changed = []
    view.changed.connect(lambda: changed.append(True))

    nit_id = ids[1]     # NIT 15, folder tmp/missing
    view._delete(nit_id, "NIT15", str(tmp / "missing"))
    check("delete targeted the shipment's folder",
          trashed and trashed[-1] == str(tmp / "missing"))
    check("record removed from the database", shipments.get(nit_id) is None)
    check("workflow steps cascade-deleted",
          ctx.db.query_one("SELECT COUNT(*) c FROM workflow_steps "
                           "WHERE shipment_id=?", (nit_id,))["c"] == 0)
    check("table rebuilt without the deleted row", view.table.rowCount() == 2)
    check("NIT no longer listed", "NIT" not in " ".join(column(view.table, 0)))
    check("changed signal fired for the dashboard", changed == [True])
    check("deletion is confirmed to the user", not view.message.isHidden()
          and "dihapus" in view.message.text())

    # A folder that cannot be removed keeps the record.
    history_module.fileops.delete_shipment_folder = lambda f: FolderDeleteResult(
        False, False, True, "Folder sedang dibuka.")
    amj_id = ids[0]
    view._delete(amj_id, "AMJ20", str(real_folder))
    check("a failed folder delete keeps the record",
          shipments.get(amj_id) is not None)
    check("a failed folder delete still lists the row", view.table.rowCount() == 2)

    window.wizard.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("History OK - all checks passed.")
