"""Container search parsing + the Containers service.

The HTML fixtures are the real containerDetail(...) onclick cards captured from
the live portal for CMAU8513405 (the plan's verified example): the container sits
at PTP on an old voyage (09-GATE OUT DELIVERY) and at TPKB on the current one
(51-STACK RECEIVING).
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.db import Database, db_path_for, new_id
from bot_kalung.services import bnct
from bot_kalung.services.containers import Containers

PTP_HTML = (
    '<a href="#" class="navi-link" onclick="containerDetail(\'1013387\','
    "'CMAU8513405','40','DRY','FCL','09','GATE OUT DELIVERY','CMA CGM SA',"
    "'ISCO007','ISEACO FORTUNE','0B825E','0B825E')\">"
    '<span>CMAU8513405 (40 /DRY)</span></a>')
TPKB_HTML = (
    '<a href="#" class="navi-link bg-warning" onclick="containerDetail(\'10647963\','
    "'CMAU8513405','40','HQ','FCL','51','STACK RECEIVING','PT ANUGRAH ANDALAN PRIMA',"
    "'MAZU0007','MV.MAO GANG GUANG ZHOU','021S','021N')\">"
    '<span>CMAU8513405 (40 /HQ)</span></a>')

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


# ---- parsing --------------------------------------------------------------
ptp = bnct.parse_containers(PTP_HTML, "PTP")
tpkb = bnct.parse_containers(TPKB_HTML, "TPKB")
check("one card parsed per fragment", len(ptp) == 1 and len(tpkb) == 1)

c = ptp[0]
check("container number parsed", c.container_no == "CMAU8513405")
check("PTP shows the old voyage status",
      c.status == "09-GATE OUT DELIVERY" and not c.at_stack_receiving)
check("PTP vessel + voyages parsed",
      c.vessel_name == "ISEACO FORTUNE" and c.voyage_out == "0B825E")

t = tpkb[0]
check("TPKB shows 51-STACK RECEIVING",
      t.status == "51-STACK RECEIVING" and t.at_stack_receiving)
check("TPKB vessel + voyages parsed",
      t.vessel_name == "MV.MAO GANG GUANG ZHOU"
      and t.voyage_in == "021S" and t.voyage_out == "021N")
check("size and type parsed", t.size == "40" and t.type == "HQ")

check("a fragment with no cards yields nothing",
      bnct.parse_containers("<div>no containers</div>", "PTP") == [])

# ---- match_container ------------------------------------------------------
both = ptp + tpkb
picked = bnct.match_container(both, "MAO GANG GUANG ZHOU", "021N")
check("match_container picks the card matching the shipment's vessel+voyage",
      picked is not None and picked.at_stack_receiving)
check("a single card is returned even without a vessel match",
      bnct.match_container(ptp, "SOME OTHER SHIP", "999X") is ptp[0])
check("no match among several cards returns None",
      bnct.match_container(both, "SOME OTHER SHIP", "999X") is None)

# ---- the Containers service ----------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    db = Database(db_path_for(Path(tmp)))
    db.initialize()
    containers = Containers(db)

    sid = new_id()
    db.execute(
        "INSERT INTO shipments (id, exporter_code, sequence_number, vessel_name, "
        "voyage, status, created_at) VALUES (?,?,?,?,?, 'active', '2026-08-14')",
        (sid, "NIT", 16, "MV.MAO GANG GUANG ZHOU", "021N"))

    containers.populate(sid, ["CMAU8513405", "TRHU5986693", ""], size="40'HC")
    rows = containers.for_shipment(sid)
    check("populate stores the non-empty container numbers", len(rows) == 2)
    check("a fresh container has no status yet",
          rows[0].status == "Belum ada data")

    containers.populate(sid, ["CMAU8513405"], size="40'HC")
    check("populate is idempotent on (shipment, container)",
          len(containers.for_shipment(sid)) == 2)

    working = containers.active_with_vessel()
    check("active_with_vessel joins the shipment's vessel",
          working and working[0]["vessel_name"] == "MV.MAO GANG GUANG ZHOU")

    target = next(r for r in containers.for_shipment(sid)
                  if r.container_no == "CMAU8513405")
    prev = containers.update_status(
        target.id, site="TPKB", status_code="09", status_text="GATE IN",
        type="HQ", checked_at="2026-08-14T10:00:00")
    check("first reading has no previous status", prev is None)

    prev = containers.update_status(
        target.id, site="TPKB", status_code="51", status_text="STACK RECEIVING",
        type="HQ", checked_at="2026-08-14T10:05:00")
    check("update returns the previous status for transition detection",
          prev == "09")
    reread = next(r for r in containers.for_shipment(sid)
                  if r.container_no == "CMAU8513405")
    check("the latest reading is stored",
          reread.at_stack_receiving and reread.last_site == "TPKB")

    # ---- correcting a mis-read number ------------------------------------
    stored = containers.set_number(reread.id, "caau0000000")  # lower on purpose
    check("set_number returns the stored (upper-cased) value",
          stored == "CAAU0000000")
    renamed = next((r for r in containers.for_shipment(sid)
                    if r.id == reread.id), None)
    check("the number is corrected and upper-cased",
          renamed is not None and renamed.container_no == "CAAU0000000")
    check("the stale BNCT reading is cleared on rename",
          renamed.last_site is None and renamed.last_status_code is None
          and renamed.status == "Belum ada data")

    try:
        containers.set_number(renamed.id, "TRHU5986693")   # already on shipment
        check("renaming to an existing number is rejected", False)
    except ValueError:
        check("renaming to an existing number is rejected", True)
    try:
        containers.set_number(renamed.id, "   ")
        check("a blank number is rejected", False)
    except ValueError:
        check("a blank number is rejected", True)

    # ---- sync reconciles containers to the VGM (source of truth) ---------
    sid2 = new_id()
    db.execute(
        "INSERT INTO shipments (id, exporter_code, sequence_number, status, "
        "created_at) VALUES (?,?,?, 'active', '2026-08-14')",
        (sid2, "HCIT", 2))
    containers.populate(sid2, ["AAAU1111111", "BBBU2222222"], size="40'HC")
    keep = next(r for r in containers.for_shipment(sid2)
                if r.container_no == "AAAU1111111")
    containers.update_status(keep.id, site="TPKB", status_code="51",
                             status_text="STACK RECEIVING", type="HQ",
                             checked_at="2026-08-14T10:00:00")

    # BBBU dropped, CCCU added (e.g. a typo fix), AAAU unchanged
    changed = containers.sync(sid2, ["AAAU1111111", "cccu3333333"], size="40'HC")
    nums = sorted(r.container_no for r in containers.for_shipment(sid2))
    check("sync reports a change", changed is True)
    check("sync adds new, drops missing, keeps unchanged",
          nums == ["AAAU1111111", "CCCU3333333"])
    kept = next(r for r in containers.for_shipment(sid2)
                if r.container_no == "AAAU1111111")
    check("sync preserves the BNCT status of an unchanged container",
          kept.at_stack_receiving and kept.last_site == "TPKB")
    check("sync is a no-op when already in sync (dupes/blanks/case ignored)",
          containers.sync(sid2, ["CCCU3333333", "", "aaau1111111"]) is False)
    check("sync never wipes on an empty read",
          containers.sync(sid2, []) is False
          and len(containers.for_shipment(sid2)) == 2)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Containers OK - all checks passed.")
