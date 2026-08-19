"""Shipment search by container number (substring) and party size.

Verifies the two search dimensions the user asked for, that matches are limited
to active shipments, and that results are de-duplicated and label-sorted.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.db import Database, db_path_for, new_id
from bot_kalung.services.containers import Containers
from bot_kalung.services.search import search_shipments

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def add_shipment(db, *, code, seq, qty, size, dest, vessel, voyage, status):
    sid = new_id()
    db.execute(
        "INSERT INTO shipments (id, exporter_code, sequence_number, vessel_name, "
        "voyage, destination_port, container_quantity, container_size_short, "
        "status, created_at) VALUES (?,?,?,?,?,?,?,?,?, '2026-08-14')",
        (sid, code, seq, vessel, voyage, dest, qty, size, status))
    return sid


with tempfile.TemporaryDirectory() as tmp:
    db = Database(db_path_for(Path(tmp)))
    db.initialize()
    containers = Containers(db)

    nit16 = add_shipment(db, code="NIT", seq=16, qty=5, size="40'HC",
                         dest="CHENNAI", vessel="INTEGRA", voyage="184E",
                         status="active")
    nit17 = add_shipment(db, code="NIT", seq=17, qty=10, size="20'",
                         dest="KARACHI", vessel="INTEGRA", voyage="184E",
                         status="active")
    done = add_shipment(db, code="HAI", seq=3, qty=5, size="40'",
                        dest="NHAVA SHEVA", vessel="X", voyage="1",
                        status="done")
    containers.populate(nit16, ["CMAU8513405"], size="40'HC")
    containers.populate(nit17, ["TRHU5986693"], size="20'")
    containers.populate(done, ["ZZZU0000000"], size="40'")

    def labels(query):
        return [r.label for r in search_shipments(db, query)]

    # ---- container number, substring + case-insensitive ------------------
    check("empty query returns nothing", search_shipments(db, "") == [])
    check("blank query returns nothing", search_shipments(db, "   ") == [])
    check("full container number matches", labels("CMAU8513405") == ["NIT16"])
    check("container number is case-insensitive", labels("cmau8513405") == ["NIT16"])
    check("container number matches by substring", labels("59866") == ["NIT17"])

    hit = search_shipments(db, "CMAU")[0]
    check("container match reports the container reason",
          hit.match.startswith("Kontainer"))
    check("result carries the exporter code for its badge", hit.code == "NIT")
    check("result label is code + sequence", hit.label == "NIT16")

    # ---- party size ------------------------------------------------------
    check("party count matches the quantity", labels("10") == ["NIT17"])
    party_hit = search_shipments(db, "10")[0]
    check("party match reports the party reason",
          party_hit.match.startswith("Party"))
    check("party size text matches", labels("40'") == ["NIT16"])

    # ---- active-only + dedup + ordering ----------------------------------
    check("a completed shipment is never returned", labels("ZZZU") == [])
    check("results are de-duplicated and label-sorted",
          labels("U") == ["NIT16", "NIT17"])

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Search OK - all checks passed.")
