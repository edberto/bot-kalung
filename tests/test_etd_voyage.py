"""ETD is a per-voyage value: parsed from BNCT, shared across a voyage, and a
manual change applies to the whole voyage (database only, no Drive)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.db import Database, db_path_for, new_id
from bot_kalung.services import bnct
from bot_kalung.services.bnct import BnctReading, BnctVessel
from bot_kalung.services.bnct_monitor import BnctMonitor
from bot_kalung.services.shipments import Shipments

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


# ---- BNCT date parsing ----------------------------------------------------
check("BNCT 'DD/MM/YYYY HH:MM' parses to ISO",
      bnct.etd_to_iso("17/08/2026 12:00") == "2026-08-17")
check("a date without a time still parses",
      bnct.etd_to_iso("03/09/2026") == "2026-09-03")
check("junk yields None", bnct.etd_to_iso("n/a") is None)
check("an impossible date yields None", bnct.etd_to_iso("31/02/2026 00:00") is None)


with tempfile.TemporaryDirectory() as tmp:
    db = Database(db_path_for(Path(tmp)))
    db.initialize()
    sh = Shipments(db)

    def make(code, seq, vessel, voyage, etd):
        sid = new_id()
        db.execute(
            "INSERT INTO shipments (id, exporter_code, sequence_number, "
            "vessel_name, voyage, etd_belawan, status, created_at) "
            "VALUES (?,?,?,?,?,?, 'active', '2026-08-14')",
            (sid, code, seq, vessel, voyage, etd))
        return sid

    a = make("NIT", 18, "INTEGRA", "184E", "2026-08-17")
    b = make("HAI", 28, "INTEGRA", "184E", "2026-08-20")   # same voyage, other ETD
    c = make("NIT", 16, "MAO GANG", "021N", "2026-08-13")  # different voyage

    # ---- manual voyage-wide change ---------------------------------------
    count = sh.set_voyage_etd("INTEGRA", "184E", "2026-08-25")
    check("voyage change reports both shipments", count == 2)
    check("every shipment on the voyage gets the new ETD",
          sh.get(a)["etd_belawan"] == "2026-08-25"
          and sh.get(b)["etd_belawan"] == "2026-08-25")
    check("a shipment on another voyage is untouched",
          sh.get(c)["etd_belawan"] == "2026-08-13")

    # ---- BNCT poll converges the ETD onto the schedule -------------------
    monitor = BnctMonitor(db)
    reading = BnctReading(
        found=True, phase="schedule", checked_at="2026-08-14T10:00:00",
        vessel=BnctVessel(site="ptp", phase="schedule", name="INTEGRA",
                          voyage_out="184E", etd="20/08/2026 12:00"))
    monitor.process(a, "NIT18", reading)
    check("a poll syncs the shipment's ETD from BNCT",
          sh.get(a)["etd_belawan"] == "2026-08-20")

    # A 'not found' reading must not wipe the ETD.
    monitor.process(b, "HAI28", BnctReading(
        found=False, phase=None, checked_at="2026-08-14T10:05:00"))
    check("a not-found reading leaves the ETD alone",
          sh.get(b)["etd_belawan"] == "2026-08-25")


print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ETD voyage OK - all checks passed.")
