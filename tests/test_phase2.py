"""Phase 2 checks: settings, LLM JSON parsing, setup wizard (headless Qt).

Drive scanning and filename derivation are covered in test_drive.py.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bot_kalung.core.db import Database, db_path_for
from bot_kalung.core.settings import Settings
from bot_kalung.services import drive
from bot_kalung.services.llm import LLMClient, LLMError, _parse_json

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "GoogleDrive"
    (root / "AMJ").mkdir(parents=True)

    db = Database(db_path_for(root))
    db.initialize()
    settings = Settings(db)

    # Settings can override the built-in folder mapping.
    settings.set("exporter_folders", {"NIT": "Custom NIT Folder"})
    (root / "Custom NIT Folder").mkdir()
    check("settings override the default folder mapping",
          drive.resolve_exporter_folder(root, "NIT", settings).name == "Custom NIT Folder")
    ok, codes = drive.validate_drive_root(root, settings)
    check("validation honors the override", ok and set(codes) == {"AMJ", "NIT"})


# ---- LLM JSON parsing -----------------------------------------------------
check("parses bare JSON", _parse_json('{"a": 1}') == {"a": 1})
check("parses fenced JSON", _parse_json('```json\n{"a": 1}\n```') == {"a": 1})
check("parses JSON embedded in prose",
      _parse_json('Here you go:\n{"a": 1}\nHope that helps') == {"a": 1})
try:
    _parse_json("not json at all")
    check("rejects non-JSON", False)
except LLMError:
    check("rejects non-JSON", True)
try:
    _parse_json("[1, 2, 3]")
    check("rejects a JSON array", False)
except LLMError:
    check("rejects a JSON array", True)

client = LLMClient("anthropic", api_key="")
ok, msg = client.test_connection()
check("missing API key fails gracefully in Indonesian",
      not ok and "Kunci API" in msg)

ollama = LLMClient("ollama", ollama_url="http://localhost:59999")
ok, msg = ollama.test_connection()
check("unreachable Ollama fails gracefully in Indonesian", not ok and "Ollama" in msg)

# ---- Wizard (headless) ----------------------------------------------------
from PyQt6.QtWidgets import QApplication

from bot_kalung.core.context import AppContext
from bot_kalung.ui.setup_wizard import SetupWizard

app = QApplication.instance() or QApplication([])

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)

    ctx = AppContext()
    wizard = SetupWizard(ctx)

    check("wizard opens on step 1", wizard.stack.currentIndex() == 0)
    check("Next disabled with no folder", not wizard.next_button.isEnabled())

    wizard.drive_root = str(root)
    wizard._refresh_step()
    check("Next enabled once folder is valid", wizard.next_button.isEnabled())

    wizard._go_next()
    check("advances to step 2", wizard.stack.currentIndex() == 1)
    check("install created on the way to step 2", ctx.db is not None)
    check("Next disabled until connection tested", not wizard.next_button.isEnabled())
    # isVisible() is False while the window is unshown; isHidden() reflects the
    # widget's own visibility flag, which is what the wizard actually sets.
    check("Skip link shown before test", not wizard.skip_link.isHidden())

    wizard._skip_llm()
    check("Skip jumps to step 3 (identity only)",
          wizard.stack.currentIndex() == 2)
    check("no contact form is collected",
          not hasattr(wizard, "nanda_email") and not hasattr(wizard, "toni_wa"))
    check("Selesai disabled until email chosen", not wizard.next_button.isEnabled())

    wizard.email_combo.setCurrentIndex(1)
    check("Selesai enabled after choosing email", wizard.next_button.isEnabled())
    check("button reads 'Selesai'", wizard.next_button.text() == "Selesai")

    wizard._finish()

    check("setup flagged complete", ctx.db.is_configured())
    check("my_email persisted",
          ctx.settings.get("my_email") == "ongkalung@gmail.com")
    check("other_worker_emails excludes mine",
          len(ctx.settings.other_worker_emails) == 2)

    reopened = AppContext()
    check("configured install reloads without the wizard", reopened.load())


print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Phase 2 OK — all checks passed.")
