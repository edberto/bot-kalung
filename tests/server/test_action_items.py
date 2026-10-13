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
check("Fumi: every exporter except HOPSON and JMI",
      should_seed("fumi", "China", "NIT") and should_seed("fumi", None, "AMJ")
      and not should_seed("fumi", "India", "HAI") and not should_seed("fumi", "India", "JMI"))
check("Phyto: except HOPSON/JMI and China/India destinations",
      should_seed("phyto", "Pakistan", "NIT") and should_seed("phyto", "Turkiye", "MFK")
      and not should_seed("phyto", "India", "NIT") and not should_seed("phyto", "China", "AMJ")
      and not should_seed("phyto", "Pakistan", "HAI"))
check("COO: every destination except Tajikistan/Uzbekistan (port checked too)",
      should_seed("coo", "India", "NIT")
      and not should_seed("coo", "Pakistan", "GGN", "Tajikistan Via Karachi")
      and not should_seed("coo", "Uzbekistan", "GGN"))
check("BL: every exporter except CKJ",
      should_seed("bl", None, "AMJ") and not should_seed("bl", "Philippines", "CKJ"))
check("DG Decla: HOPSON and JMI only",
      should_seed("dg", "China", "JMI") and not should_seed("dg", "Pakistan", "NIT"))
check("Insurance: HOPSON and JMI only",
      should_seed("insurance", "India", "HAI") and should_seed("insurance", "China", "JMI")
      and not should_seed("insurance", "Pakistan", "AMJ"))
check("Marking is no longer a seed item", should_seed("marking", "India", "HAI")
      and "marking" not in [c for c, _ in __import__("bot_kalung.core.constants",
                                                      fromlist=["x"]).ACTION_ITEM_SEEDS])


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
    check("seed order follows the template (SI first, BL last of built-ins; no Dok Kirim)",
          nit_codes[0] == "si_vgm" and nit_codes[-1] == "bl")

    # HAI (HOPSON) to India: no Fumi, no Phyto; COO yes; DG Decla yes.
    hai = new_shipment("HAI")
    items.seed(hai, "HAI", "India")
    hai_codes = [i.code for i in items.list(hai)]
    check("India HAI: no Fumi/Phyto, COO yes",
          "fumi" not in hai_codes and "phyto" not in hai_codes and "coo" in hai_codes)
    check("HOPSON gets DG Decla (and no Marking)",
          "dg" in hai_codes and "marking" not in hai_codes
          and [i.title for i in items.list(hai) if i.code == "dg"] == ["DG Decla"])
    check("re-seeding adds nothing and never duplicates",
          items.seed(hai, "HAI", "India") == []
          and len(items.list(hai)) == len(hai_codes))
    items.delete(next(i.id for i in items.list(hai) if i.code == "coo"))
    check("re-seeding adds back only what's missing",
          items.seed(hai, "HAI", "India") == ["coo"])

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

    second = items.list(nit)[1]
    items.set_status(second.id, "waiting_hardcopy")
    done, _ = items.progress(nit)
    check("waiting for hardcopy is NOT done (hardcopy still outstanding)",
          done == 1 and not {i.id: i for i in items.list(nit)}[second.id].is_done)
    items.set_status(second.id, "hardcopy_received")
    done, _ = items.progress(nit)
    check("hardcopy received counts as done", done == 2)

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
