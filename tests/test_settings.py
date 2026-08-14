"""Settings screen — two tabs (Umum, LLM) after the folder-scan refactor."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from bot_kalung.core.constants import WORKER_EMAILS
from bot_kalung.core.context import AppContext
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

    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set_many({"setup_complete": "1", "my_email": WORKER_EMAILS[0]})

    window = MainWindow(ctx)
    window.open_settings()
    view = window.settings

    check("settings reachable", window.stack.currentIndex() == VIEW_SETTINGS)
    check("two tabs after the refactor", view.tabs.count() == 2)
    check("tab titles", [view.tabs.tabText(i) for i in range(2)] == ["Umum", "LLM"])
    check("the retired tabs are gone",
          not hasattr(view, "country_list")
          and not hasattr(view, "subject_fields")
          and not hasattr(view, "folder_fields"))

    # ---- loaded state ----------------------------------------------------
    check("drive path loaded", view.drive_field.text() == str(root))
    check("my email loaded", view.email_combo.currentData() == WORKER_EMAILS[0])
    check("provider defaults to anthropic", view.radio_anthropic.isChecked())
    check("ollama fields disabled while anthropic selected",
          not view.ollama_model_field.isEnabled())

    # ---- drive validation ------------------------------------------------
    check("valid drive root passes", view._validate_drive())
    check("validation reports which exporters were found",
          "AMJ" in view.drive_message.text())
    view.drive_field.setText(str(tmp / "nope"))
    check("missing drive root fails", not view._validate_drive())
    check("missing root reports an error", not view.drive_message.isHidden())
    view.drive_field.setText(str(root))

    # ---- LLM -------------------------------------------------------------
    view.radio_ollama.setChecked(True)
    view._on_provider_changed()
    check("switching provider enables ollama fields",
          view.ollama_model_field.isEnabled()
          and not view.api_key_field.isEnabled())
    view.radio_anthropic.setChecked(True)
    view._on_provider_changed()
    view.api_key_field.setText("sk-ant-test-key")

    # ---- BNCT interval + ntfy --------------------------------------------
    view.bnct_interval_field.setValue(7)
    view.ntfy_enabled.setChecked(True)

    # ---- save ------------------------------------------------------------
    view.save()
    check("save reports success", not view.message.isHidden()
          and "disimpan" in view.message.text())

    settings = ctx.settings
    check("api key persisted", settings.get("llm_api_key") == "sk-ant-test-key")
    check("provider persisted", settings.get("llm_provider") == "anthropic")
    check("bnct interval persisted", settings.get("bnct_interval_minutes") == "7")
    check("ntfy toggle persisted", settings.get_bool("ntfy_enabled"))

    # ---- reload round-trip -----------------------------------------------
    view.load()
    check("api key reloaded", view.api_key_field.text() == "sk-ant-test-key")
    check("interval reloaded", view.bnct_interval_field.value() == 7)

    # A second AppContext must see everything, since the DB is the shared store.
    other = AppContext()
    other.attach(root)
    check("another session sees the saved key",
          other.settings.get("llm_api_key") == "sk-ant-test-key")

    # ---- back navigation -------------------------------------------------
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
