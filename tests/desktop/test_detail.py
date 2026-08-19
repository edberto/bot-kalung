"""Shipment Detail header and quarantine banner (PRD 6.1, 6.1.1)."""

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from bot_kalung.core.context import AppContext
from bot_kalung.services.action_items import ActionItems
from bot_kalung.services.shipments import Shipments
from bot_kalung.ui.main_window import VIEW_DETAIL, MainWindow

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


app = QApplication.instance() or QApplication([])

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    root = tmp / "Drive"
    (root / "AMJ").mkdir(parents=True)

    # A folder that looks like a real shipment folder.
    folder = root / "AMJ" / "2026" / "24.3x40'-karachi-2331048250-Integra 181E-25 jul"
    folder.mkdir(parents=True)
    (folder / "AMJ24-VGM,SI,Inv,PL.xls").write_text("x", encoding="utf-8")
    (folder / "Invoice tagihan AMJ24.xlsx").write_text("x", encoding="utf-8")

    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set("setup_complete", "1")
    shipments = Shipments(ctx.db)
    items = ActionItems(ctx.db)

    quarantined = shipments.create({
        "exporter_code": "AMJ", "sequence_number": 24,
        "booking_number": "2331048250", "vessel_name": "INTEGRA", "voyage": "181E",
        "etd_belawan": (date.today() + timedelta(days=2)).isoformat(),
        "destination_port": "KARACHI", "destination_country": "PAKISTAN",
        "container_quantity": 3, "container_size_short": "40'",
        "quarantine_required": True, "folder_path": str(folder),
    }, seed_steps=False)
    items.seed(quarantined, "AMJ", "Pakistan")
    plain = shipments.create({
        "exporter_code": "NIT", "sequence_number": 16,
        "booking_number": "T22854", "vessel_name": "MTT REYA", "voyage": "123N",
        "etd_belawan": (date.today() + timedelta(days=30)).isoformat(),
        "destination_port": "CHENNAI", "destination_country": "INDIA",
        "container_quantity": 10, "container_size_short": "40'",
        "quarantine_required": False, "folder_path": str(tmp / "gone"),
    }, seed_steps=False)
    items.seed(plain, "NIT", "India")

    window = MainWindow(ctx)
    window.open_shipment(quarantined)
    detail = window.detail

    check("navigates to the detail view", window.stack.currentIndex() == VIEW_DETAIL)
    check("badge shows exporter and sequence", detail.badge.text() == "AMJ24")
    check("vessel and voyage shown", detail.vessel_label.text() == "INTEGRA 181E")
    check("ETD in Indonesian", "ETD" in detail.etd_label.text()
          and "-" not in detail.etd_label.text().replace("ETD ", ""))
    check("ETD red when three days out or less",
          "#dc2626" in detail.etd_label.styleSheet())
    check("booking, destination and containers in the meta line",
          "2331048250" in detail.meta_label.text()
          and "KARACHI" in detail.meta_label.text()
          and "3 x 40'" in detail.meta_label.text())

    done, total = items.progress(quarantined)
    check("progress reflects the seeded action items",
          detail.progress_label.text() == f"{done} dari {total} item final")
    check("progress bar matches", detail.progress_bar.value() == done)

    check("folder resolved", detail.folder == folder)
    check("workbook found in the folder",
          detail.workbook is not None
          and detail.workbook.name == "AMJ24-VGM,SI,Inv,PL.xls")
    check("invoice not mistaken for the main workbook",
          "Invoice" not in (detail.workbook.name if detail.workbook else ""))
    check("Open Folder enabled", detail.folder_button.isEnabled())
    check("Open Excel enabled", detail.excel_button.isEnabled())

    check("quarantine banner shown", not detail.quarantine_banner.isHidden())
    check("quarantine banner mentions the quarantine documents",
          "Karantina" in detail.quarantine_banner.text())

    # A shipment with no quarantine and a missing folder.
    window.open_shipment(plain)
    check("no quarantine banner for a non-listed country",
          detail.quarantine_banner.isHidden())
    check("ETD not red when far out", "#dc2626" not in detail.etd_label.styleSheet())
    check("missing folder disables Open Excel", not detail.excel_button.isEnabled())
    detail._open_folder()
    check("opening a missing folder reports an error",
          not detail.message.isHidden())

    # A shipment id that no longer exists must not crash the view.
    detail.load("does-not-exist")
    check("unknown shipment reports an error rather than crashing",
          not detail.message.isHidden())

    # ---- delete a shipment (folder + record) -----------------------------
    from PyQt6.QtWidgets import QMessageBox

    from bot_kalung.services.fileops import FolderDeleteResult
    from bot_kalung.ui import shipment_detail

    # Don't touch the real Recycle Bin from a test, and auto-confirm the
    # modal so the flow runs headless; record what the handler was told to
    # delete so the folder path can be asserted.
    trashed = []
    shipment_detail.fileops.delete_shipment_folder = lambda f: (
        trashed.append(f)
        or FolderDeleteResult(True, True, bool(f), "Folder dipindahkan ke Recycle Bin."))
    QMessageBox.exec = lambda self: QMessageBox.StandardButton.Yes
    # The dashboard shows a modal confirmation after deletion; don't block on it.
    QMessageBox.information = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Ok)

    notes = []
    detail.deleted.connect(notes.append)

    def item_rows(sid):
        return ctx.db.query_one(
            "SELECT COUNT(*) c FROM action_items WHERE shipment_id=?", (sid,))["c"]

    window.open_shipment(plain)
    check("shipment has action items before deletion", item_rows(plain) > 0)

    detail._delete_shipment()
    check("the shipment's own folder was the delete target",
          trashed and trashed[-1] == str(tmp / "gone"))
    check("record is gone from the database", shipments.get(plain) is None)
    check("action items are cascade-deleted", item_rows(plain) == 0)
    check("the delete signal carried a note",
          notes and "dihapus" in notes[-1])
    check("view returns to the dashboard",
          window.stack.currentIndex() != VIEW_DETAIL)
    check("the other shipment is untouched", shipments.get(quarantined) is not None)

    # A folder that cannot be removed keeps the record intact.
    window.open_shipment(quarantined)
    shipment_detail.fileops.delete_shipment_folder = lambda f: FolderDeleteResult(
        False, False, True, "Folder sedang dibuka.")
    detail._delete_shipment()
    check("a failed folder delete keeps the record",
          shipments.get(quarantined) is not None)
    check("a failed folder delete surfaces the reason",
          not detail.message.isHidden())

    # ---- BNCT notification deep-link -------------------------------------
    from bot_kalung.ui.main_window import VIEW_DASHBOARD

    window.open_dashboard()
    check("starts on the dashboard", window.stack.currentIndex() == VIEW_DASHBOARD)
    # Simulate a tray toast for the quarantined shipment being clicked.
    window._notified_shipment = quarantined
    window._open_notified_shipment()
    check("clicking the notification opens its shipment",
          window.stack.currentIndex() == VIEW_DETAIL
          and window.current_shipment_id == quarantined)

    # A notification for a since-deleted shipment must not crash or navigate.
    window.open_dashboard()
    window._focus_shipment(plain)     # plain was deleted earlier
    check("a deep-link to a deleted shipment is ignored",
          window.stack.currentIndex() == VIEW_DASHBOARD)
    window._focus_shipment(None)
    check("a deep-link with no shipment is ignored",
          window.stack.currentIndex() == VIEW_DASHBOARD)

    window.wizard.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Shipment detail OK - all checks passed.")
