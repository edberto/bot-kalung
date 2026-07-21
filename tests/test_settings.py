"""Settings screen (PRD Section 11, narrowed — see DECISIONS.md 13)."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from bot_kalung.core.constants import DEFAULT_EXPORTER_FOLDERS, WORKER_EMAILS
from bot_kalung.core.context import AppContext
from bot_kalung.services import drive
from bot_kalung.ui.main_window import VIEW_SETTINGS, MainWindow

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
    (root / "Pengangkut Baru").mkdir()

    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set_many({"setup_complete": "1", "my_email": WORKER_EMAILS[0]})

    window = MainWindow(ctx)
    window.open_settings()
    view = window.settings

    check("settings reachable", window.stack.currentIndex() == VIEW_SETTINGS)
    check("four tabs, contacts dropped", view.tabs.count() == 4)
    check("tab titles", [view.tabs.tabText(i) for i in range(4)]
          == ["Umum", "LLM", "Subjek Email", "Negara Karantina"])

    # ---- loaded state ----------------------------------------------------
    check("drive path loaded", view.drive_field.text() == str(root))
    check("my email loaded", view.email_combo.currentData() == WORKER_EMAILS[0])
    check("provider defaults to anthropic", view.radio_anthropic.isChecked())
    check("ollama fields disabled while anthropic selected",
          not view.ollama_model_field.isEnabled())
    check("quarantine list loaded", view.country_list.count() == 1
          and view.country_list.item(0).text() == "PAKISTAN")
    check("email subject fields built",
          len(view.subject_fields) == 8 and "T10" in view.subject_fields)
    check("subject prefilled as exporter+seq only",
          view.subject_fields["T10"].text() == "{exporter}{seq}")

    # ---- drive validation --------------------------------------------------
    check("valid drive root passes", view._validate_drive())
    check("validation reports which exporters were found",
          "AMJ" in view.drive_message.text())
    view.drive_field.setText(str(tmp / "nope"))
    check("missing drive root fails", not view._validate_drive())
    check("missing root reports an error", not view.drive_message.isHidden())
    view.drive_field.setText(str(root))

    # ---- LLM ---------------------------------------------------------------
    view.radio_ollama.setChecked(True)
    view._on_provider_changed()
    check("switching provider enables ollama fields",
          view.ollama_model_field.isEnabled()
          and not view.api_key_field.isEnabled())
    view.radio_anthropic.setChecked(True)
    view._on_provider_changed()

    view.api_key_field.setText("sk-ant-test-key")

    # ---- quarantine editing -------------------------------------------------
    view.country_field.setText("india")
    view._add_country()
    check("country added and upper-cased", view.country_list.count() == 2
          and view.country_list.item(1).text() == "INDIA")
    view.country_field.setText("INDIA")
    view._add_country()
    check("duplicate rejected", view.country_list.count() == 2)
    check("duplicate warns", not view.message.isHidden())
    view.country_list.setCurrentRow(1)
    view._remove_country()
    check("country removed", view.country_list.count() == 1)

    view.country_field.setText("bangladesh")
    view._add_country()

    # ---- exporter folder mapping ---------------------------------------------
    view.folder_fields["NIT"].setText("Pengangkut Baru")

    # ---- subject editing -------------------------------------------------------
    view.subject_fields["T10"].setText("Dokumen {exporter}{seq} - {vessel_voyage}")

    # ---- save -----------------------------------------------------------------
    view.save()
    check("save reports success", not view.message.isHidden()
          and "disimpan" in view.message.text())

    settings = ctx.settings
    check("api key persisted", settings.get("llm_api_key") == "sk-ant-test-key")
    check("provider persisted", settings.get("llm_provider") == "anthropic")
    check("quarantine list persisted",
          set(settings.quarantine_countries) == {"PAKISTAN", "BANGLADESH"})
    check("quarantine check uses the saved list",
          settings.is_quarantine_country("bangladesh")
          and not settings.is_quarantine_country("india"))

    mapping = settings.get("exporter_folders")
    check("overridden exporter folder persisted",
          mapping.get("NIT") == "Pengangkut Baru")
    check("unchanged exporters are not stored",
          "AMJ" not in mapping and "TTJ" not in mapping)
    check("mapping actually resolves the new folder",
          drive.resolve_exporter_folder(root, "NIT", settings) is not None
          and drive.resolve_exporter_folder(root, "NIT", settings).name
          == "Pengangkut Baru")
    check("defaults still apply to untouched exporters",
          drive.exporter_folder_map(settings)["AMJ"]
          == DEFAULT_EXPORTER_FOLDERS["AMJ"])

    row = ctx.db.query_one(
        "SELECT subject_template FROM message_templates WHERE id='T10'")
    check("subject persisted",
          row["subject_template"] == "Dokumen {exporter}{seq} - {vessel_voyage}")

    # ---- reload round-trip -------------------------------------------------------
    view.load()
    check("api key reloaded", view.api_key_field.text() == "sk-ant-test-key")
    check("quarantine reloaded", view.country_list.count() == 2)
    check("exporter folder reloaded",
          view.folder_fields["NIT"].text() == "Pengangkut Baru")

    # A second AppContext must see everything, since the DB is the shared store.
    other = AppContext()
    other.attach(root)
    check("another session sees the saved key",
          other.settings.get("llm_api_key") == "sk-ant-test-key")
    check("another session sees the saved quarantine list",
          "BANGLADESH" in other.settings.quarantine_countries)

    # ---- back navigation ---------------------------------------------------------
    window.open_history()
    window.open_settings()
    view.back_requested.emit()
    check("Back returns to the previous view",
          window.stack.currentIndex() != VIEW_SETTINGS)

    view.shutdown()
    window.wizard.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Settings OK - all checks passed.")
