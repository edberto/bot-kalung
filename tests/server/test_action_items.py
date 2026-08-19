"""Action items: conditional seeding, status changes, custom add/delete."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.db import Database, db_path_for, new_id
from bot_kalung.services.action_items import ActionItems, should_seed

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


# ---- conditional rules ----------------------------------------------------
check("Fumi applies to Pakistan", should_seed("fumi", "Pakistan", "NIT"))
check("Fumi applies to India", should_seed("fumi", "India", "NIT"))
check("Fumi excludes China", not should_seed("fumi", "China", "JMI"))
check("Phyto applies to Philippines", should_seed("phyto", "Philippines", "CKJ"))
check("Phyto excludes India", not should_seed("phyto", "India", "NIT"))
check("COO excludes India", not should_seed("coo", "India", "NIT"))
check("Marking applies to HAI (HOPSON)", should_seed("marking", "Taiwan", "HAI"))
check("DG applies to JMI", should_seed("dg", "China", "JMI"))
check("DG excludes NIT", not should_seed("dg", "Pakistan", "NIT"))
check("always-on items ignore country", should_seed("bl", None, "AMJ"))
check("country-gated items drop when country is unknown",
      not should_seed("fumi", None, "NIT"))


with tempfile.TemporaryDirectory() as tmp:
    db = Database(db_path_for(Path(tmp)))
    db.initialize()
    items = ActionItems(db)

    def new_shipment(code):
        sid = new_id()
        db.execute(
            "INSERT INTO shipments (id, exporter_code, sequence_number, status, "
            "created_at) VALUES (?,?,?, 'active', '2026-08-14')", (sid, code, 1))
        return sid

    # NIT to Pakistan: SI/VGM, Fumi, Phyto, COO, PEB&NPE, BL, Dok Kirim (no DG/Marking).
    nit = new_shipment("NIT")
    items.seed(nit, "NIT", "Pakistan")
    nit_codes = [i.code for i in items.list(nit)]
    check("Pakistan NIT seeds the quarantine trio",
          {"fumi", "phyto", "coo"} <= set(nit_codes))
    check("NIT gets no Marking/DG",
          "marking" not in nit_codes and "dg" not in nit_codes)
    check("seed order follows the template (SI first, Dok Kirim last of built-ins)",
          nit_codes[0] == "si_vgm" and nit_codes[-1] == "dok_kirim")

    # HAI (HOPSON) to India: Fumi yes, Phyto/COO no, Marking + DG yes.
    hai = new_shipment("HAI")
    items.seed(hai, "HAI", "India")
    hai_codes = [i.code for i in items.list(hai)]
    check("India HAI seeds Fumi but not Phyto/COO",
          "fumi" in hai_codes and "phyto" not in hai_codes and "coo" not in hai_codes)
    check("HOPSON gets Marking + DG",
          "marking" in hai_codes and "dg" in hai_codes)

    # ---- status changes + progress ---------------------------------------
    first = items.list(nit)[0]
    check("items start pending", first.status == "pending")
    done, total = items.progress(nit)
    check("nothing is done initially", done == 0 and total == len(nit_codes))

    items.set_status(first.id, "final")
    reread = {i.id: i for i in items.list(nit)}[first.id]
    check("status persists", reread.status == "final" and reread.is_done)
    done, _ = items.progress(nit)
    check("progress counts the final item", done == 1)

    try:
        items.set_status(first.id, "bogus")
        check("an unknown status is rejected", False)
    except ValueError:
        check("an unknown status is rejected", True)

    # ---- custom add + delete ---------------------------------------------
    custom_id = items.add_custom(nit, "Kirim contoh ke pembeli")
    codes_now = [i for i in items.list(nit) if i.id == custom_id]
    check("a custom item is added at the end and flagged custom",
          codes_now and codes_now[0].is_custom
          and items.list(nit)[-1].id == custom_id)

    items.delete(custom_id)
    check("a deleted item is gone",
          custom_id not in {i.id for i in items.list(nit)})
    items.delete(first.id)
    check("a built-in item can also be deleted",
          first.id not in {i.id for i in items.list(nit)})


print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Action items OK - all checks passed.")
