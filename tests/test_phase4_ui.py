"""Phase 4 wizard UI: step gating, field population, sequence detection,
folder naming and the shipment record it writes (headless Qt).

The LLM step is bypassed — extraction is covered by feeding the wizard an
ExtractedDO directly, the same object the worker thread emits.
"""

import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from bot_kalung.core.context import AppContext
from bot_kalung.services.do_parser import extract_fields, extract_text
from bot_kalung.services.shipments import Shipments
from bot_kalung.ui.new_shipment import NewShipmentWizard

LIVE_AMJ = Path(r"G:\My Drive\AMJ\2026"
                r"\23.2x40-karachi-Salim-KLN-EVE-084600048570-Concert 0800-088N-03 aug")
LIVE_DO = Path(r"G:\My Drive\AMJ\2026"
               r"\18.3x40-KARACHI-Taheer-SUNLIE-OOCL-2331048250-INTEGRA181"
               r"\2331048250 (1).pdf")

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


app = QApplication.instance() or QApplication([])

# ---- DO text extraction (no LLM needed) ----------------------------------
if LIVE_DO.is_file():
    text = extract_text(LIVE_DO)
    check("pdfplumber reads the OOCL DO", len(text) > 5000)
    check("booking number present in extracted text", "2331048250" in text)
    check("vessel/voyage present", "INTEGRA 181E" in text)
else:
    print("SKIP  live DO pdf not reachable")


# ---- post-processing of an LLM response -----------------------------------
class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def extract_do_fields(self, raw_text):
        return self.payload


full = extract_fields(FakeClient({
    "booking_number": "2331048250", "vessel_name": "INTEGRA", "voyage": "181E",
    "etd_belawan": "2026-07-25", "destination_port": "Karachi",
    "destination_country": "Pakistan", "container_quantity": 3,
    "container_size_raw": "40' Hi-Cube Container",
    "empty_pickup_location": "PT Samudera Sarana Logistik",
}), "text")
check("complete extraction reports no missing fields", full.missing == [])
check("extraction is considered complete", full.is_complete)
check("size shortened during post-processing", full.container_size_short == "40'")
check("etd parsed to a date", full.etd_belawan == date(2026, 7, 25))
check("quantity parsed", full.container_quantity == 3)

partial = extract_fields(FakeClient({
    "booking_number": "2331048250", "vessel_name": None, "voyage": "",
    "etd_belawan": "not-a-date", "destination_port": "Karachi",
    "destination_country": None, "container_quantity": "abc",
    "container_size_raw": None, "empty_pickup_location": None,
}), "text")
check("partial extraction lists what is missing",
      set(partial.missing) >= {"vessel_name", "voyage", "etd_belawan",
                               "destination_country", "container_size_raw"})
check("partial extraction is not complete", not partial.is_complete)
check("bad quantity falls back to 1", partial.container_quantity == 1)
check("optional pickup never counted as missing",
      "empty_pickup_location" not in partial.missing)

# ---- wizard flow -----------------------------------------------------------
if not LIVE_AMJ.is_dir():
    print("SKIP  live AMJ folder not reachable; wizard flow not exercised")
else:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "Drive"
        amj_year = root / "AMJ" / "2026"
        amj_year.mkdir(parents=True)
        shutil.copytree(LIVE_AMJ, amj_year / LIVE_AMJ.name)

        ctx = AppContext()
        ctx.create(root)
        ctx.settings.set("setup_complete", "1")
        shipments = Shipments(ctx.db)

        wizard = NewShipmentWizard(ctx)

        # -- step 1 gating ---------------------------------------------------
        check("wizard opens on step 1", wizard.stack.currentIndex() == 0)
        check("Next disabled with nothing selected",
              not wizard.next_button.isEnabled())

        wizard._choose_exporter("AMJ")
        check("exporter selection registers", wizard.exporter == "AMJ")
        check("selected card is highlighted",
              wizard.exporter_cards["AMJ"].selected
              and not wizard.exporter_cards["NIT"].selected)
        check("Next still disabled without a PDF",
              not wizard.next_button.isEnabled())

        do_copy = tmp / "do.pdf"
        do_copy.write_bytes(b"%PDF-1.4 placeholder")
        wizard._choose_pdf(str(do_copy))
        check("Next enabled once exporter and PDF are set",
              wizard.next_button.isEnabled())
        check("remove link appears with a file", not wizard.remove_button.isHidden())

        wizard._choose_pdf(None)
        check("removing the file disables Next again",
              not wizard.next_button.isEnabled())
        wizard._choose_pdf(str(do_copy))

        # -- step 2, as if extraction had succeeded ---------------------------
        wizard._on_extraction_done(full, "")
        check("advances to step 2", wizard.stack.currentIndex() == 1)
        check("booking pre-filled", wizard.booking_field.text() == "2331048250")
        check("vessel pre-filled", wizard.vessel_field.text() == "INTEGRA")
        check("etd pre-filled",
              wizard.etd_field.date().toString("yyyy-MM-dd") == "2026-07-25")
        check("quantity pre-filled", wizard.quantity_field.value() == 3)
        check("size pre-filled", wizard.size_field.text() == "40'")

        check("sequence auto-detected as 24", wizard.sequence_field.value() == 24)
        check("sequence note names the source folder",
              "23" in wizard.sequence_note.text())
        check("source folder resolved to the AMJ23 folder",
              wizard.source_folder is not None
              and wizard.source_folder.name.startswith("23."))

        check("quarantine banner shown for Pakistan",
              not wizard.quarantine_message.isHidden())
        wizard.dest_country_field.setText("Singapore")
        check("quarantine banner cleared for a non-listed country",
              wizard.quarantine_message.isHidden())
        wizard.dest_country_field.setText("Pakistan")

        # Shipping company is free text, pre-filled from the DO's carrier, and
        # never blocks: contacts are not configured, so there is no list.
        check("Next enabled without a shipping company",
              wizard.next_button.isEnabled())
        check("no carrier in this fixture leaves the field empty",
              wizard.shipping_field.text() == "")
        check("unknown carrier is reported",
              not wizard.carrier_message.isHidden())

        wizard._apply_carrier("EVERGREEN")
        check("a detected carrier fills the field",
              wizard.shipping_field.text() == "EVERGREEN")
        check("detection is reported", "EVERGREEN" in wizard.carrier_message.text())

        wizard.shipping_field.setText("")
        check("blank shipping company still allows Next",
              wizard.next_button.isEnabled())
        wizard.shipping_field.setText("OOCL")
        check("the field is editable", wizard.shipping_field.text() == "OOCL")

        # a required field cleared must block again
        wizard.booking_field.setText("")
        check("clearing a required field blocks Next",
              not wizard.next_button.isEnabled())
        wizard.booking_field.setText("2331048250")

        # -- step 3 -----------------------------------------------------------
        wizard._go_next()
        check("advances to step 3", wizard.stack.currentIndex() == 2)
        expected = "24.3x40'-karachi-2331048250-Integra 181E-25 jul"
        check(f"folder name follows PRD 9.3 -> {wizard._folder_name()}",
              wizard._folder_name() == expected)
        check("summary names the shipment",
              "AMJ24" in wizard.summary.text())
        check("plan lists the source folder",
              "23." in wizard.plan.text())

        # -- extraction failure path -------------------------------------------
        wizard2 = NewShipmentWizard(ctx)
        wizard2._choose_exporter("AMJ")
        wizard2._choose_pdf(str(do_copy))
        wizard2._on_extraction_done(None, "Kunci API tidak valid.")
        check("failed extraction still advances to step 2",
              wizard2.stack.currentIndex() == 1)
        check("failed extraction shows the error",
              not wizard2.extract_message.isHidden())
        check("failed extraction leaves fields empty",
              wizard2.booking_field.text() == "")
        check("failed extraction blocks Next until filled",
              not wizard2.next_button.isEnabled())

        # -- partial extraction warning ------------------------------------------
        wizard3 = NewShipmentWizard(ctx)
        wizard3._choose_exporter("AMJ")
        wizard3._choose_pdf(str(do_copy))
        wizard3._on_extraction_done(partial, "")
        check("partial extraction warns but proceeds",
              wizard3.stack.currentIndex() == 1
              and not wizard3.extract_message.isHidden())

        # Background permit checks hold the PDF open; wait for them before the
        # temp directory is torn down.
        for w in (wizard, wizard2, wizard3):
            w.shutdown()
        check("workers cleared after shutdown", wizard.workers == [])

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Phase 4 UI OK - all checks passed.")
