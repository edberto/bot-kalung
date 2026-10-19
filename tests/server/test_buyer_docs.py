"""AMI / PMA: the buyer's Dok AMI / Dok PMA documents override the workbook —
with a fake PDF text and a fake reader, no network."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.db import Database, db_path_for
from bot_kalung.services import buyer_docs, doc_checks
from bot_kalung.services.excel import ShipmentFields

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


READ = {"vessel": "MTT REYA", "voyage": "26RY147N", "pod": "CHENNAI PORT", "country": "INDIA",
        "booking": "BLWSPMB000361", "size": "40'HC", "quantity": 4,
        "containers": [{"number": "MISU5065640", "seal": "SPM781788", "tare_kg": 3840}],
        "numbers": {"SI": "036/AMI/X/26", "VGM": "036/AMI/X/26", "CI": "036/AMI/X/26", "PL": None}}
buyer_docs._text = lambda data: "SHIPPING INSTRUCTION No. 036/AMI/X/26"   # no real PDF needed

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    db = Database(db_path_for(root / "db"))
    db.initialize()
    folder = root / "9.AMI036-x"
    (folder / "Dok AMI").mkdir(parents=True)
    (folder / "Dok AMI" / "SI 36 JAI SHREE RAM.pdf").write_bytes(b"%PDF")
    (folder / "Dok AMI" / "Rev ke 3-CI 36.pdf").write_bytes(b"%PDF")
    excel = lambda: ShipmentFields(vessel_name="OLD", voyage="1N", booking_number="X",   # noqa: E731
                                   containers=["AAAA1111111"], reference_text="workbook")
    doc_checks.new_scan()
    db.execute("DELETE FROM ai_reads")
    f = buyer_docs.apply(db, str(folder), "AMI", excel(), ask=lambda text: READ)
    check("vessel/voyage, destination, booking, party come from the buyer's documents",
          (f.vessel_name, f.voyage, f.destination_port, f.destination_country, f.booking_number,
           f.container_quantity, f.container_size_short)
          == ("MTT REYA", "26RY147N", "Chennai", "India", "BLWSPMB000361", 4, "40'HC"))
    check("...the containers with seal and tare (VGM)",
          f.containers == ["MISU5065640"] and f.vgm_containers_read
          and f.vgm_rows == {"MISU5065640": {"seal": "SPM781788", "tare": "3840"}})
    check("...the document numbers (VGM, CI; PL absent)",
          f.doc_numbers == {"docs": [("VGM (dok buyer)", "vgm", "036/AMI/X/26"),
                                     ("CI (dok buyer)", "inv", "036/AMI/X/26")]})
    db.execute("DELETE FROM ai_reads")
    nov = {**READ, "pod": "CHENNAI PORT", "numbers": {"SI": "036/AMI/X/26", "VGM": None,
                                                      "CI": "036/AMI/X/26", "PL": "036/AMI/X/26"}}
    g = buyer_docs.apply(db, str(folder), "AMI", excel(), ask=lambda text: nov)
    check("no VGM number (the buyer's VGM has none): the SI is the reference",
          [d[0] for d in g.doc_numbers["docs"]] == ["SI (dok buyer)", "CI (dok buyer)", "PL (dok buyer)"])
    check("'CHENNAI PORT' is stored as 'Chennai', like the workbook", g.destination_port == "Chennai")
    check("...and drafts are checked against the buyer's documents, named and dated",
          "=== file: SI 36 JAI SHREE RAM.pdf" in f.reference_text
          and "Rev ke 3-CI 36.pdf" in f.reference_text and "workbook" not in f.reference_text)
    check("other exporters keep the workbook", buyer_docs.apply(
        db, str(folder), "AMJ", excel(), ask=lambda t: READ).vessel_name == "OLD")
    empty = root / "10.AMI037-y"
    (empty / "Dok AMI").mkdir(parents=True)
    check("an empty Dok AMI folder keeps the workbook",
          buyer_docs.apply(db, str(empty), "AMI", excel(), ask=lambda t: READ).vessel_name == "OLD")
    db.execute("DELETE FROM ai_reads")
    check("unreadable now (no key / budget): the workbook's details stand",
          buyer_docs.apply(db, str(folder), "AMI", excel(), ask=lambda t: None).vessel_name == "OLD")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Buyer docs OK - all checks passed.")
