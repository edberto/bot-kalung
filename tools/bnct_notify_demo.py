"""Demo: see the real BNCT departure notification without waiting for a vessel.

Runs the actual app (MainWindow) but swaps the live portal client for a fake
one that reports the shipment's vessel as *alongside and departing* (Loading
Remain below the threshold). You get the app's real system-tray notifications
and the "pay LOLO" dialog — the exact production code path — so you can confirm
notifications actually surface on this machine.

    .venv\\Scripts\\python tools\\bnct_notify_demo.py

Uses a temporary database and redirects the machine-local pointer, so it never
touches your real Google Drive or config. Close the window to exit.
"""

import os
import sys
import tempfile
from pathlib import Path

# Keep the demo off the real config / Drive, and use a real (non-offscreen) Qt
# platform so the tray balloon actually shows.
os.environ["BOTKALUNG_HOME"] = tempfile.mkdtemp(prefix="bnct-demo-home-")
os.environ.pop("QT_QPA_PLATFORM", None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from bot_kalung import main as app_main
from bot_kalung.core.context import AppContext
from bot_kalung.services.bnct import BnctVessel
from bot_kalung.services.shipments import Shipments
from bot_kalung.ui.main_window import MainWindow


class DepartingClient:
    """Pretends the shipment's vessel is alongside with only 2 boxes left."""

    def fetch_vessels(self):
        return [BnctVessel(
            site="tpkb", phase="alongside", name="MV. MTT REYA",
            voyage_in="26RY123S", voyage_out="26RY123N",
            loading_plan=800, loading_actual=798, loading_remain=2,
            discharge_plan=500, discharge_actual=500, discharge_remain=0,
            atb="21/07/2026 06:00", etd="25/07/2026 12:00")]


def main():
    app = QApplication.instance() or QApplication([])
    app_main.apply_icon(app)

    root = Path(tempfile.mkdtemp(prefix="bnct-demo-drive-")) / "Drive"
    (root / "AMJ").mkdir(parents=True)
    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set("setup_complete", "1")

    Shipments(ctx.db).create({
        "exporter_code": "NIT", "sequence_number": 15, "vessel_name": "MTT REYA",
        "voyage": "123N", "booking_number": "T22854", "etd_belawan": "2026-07-25",
        "destination_port": "CHENNAI", "destination_country": "INDIA",
        "container_quantity": 2, "container_size_short": "40'",
    })

    window = MainWindow(ctx)
    window.bnct.stop()                        # stop the real (live-portal) polling
    window.bnct.client = DepartingClient()    # ...and swap in the fake
    window.show()

    QTimer.singleShot(800, window.bnct.poll_now)
    print("Demo running. Watch for a system-tray notification and a 'pay LOLO' "
          "dialog. Close the window to exit.")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
