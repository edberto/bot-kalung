"""Theme tokens, stylesheet translation, and the Settings toggle."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication

from bot_kalung.core.context import AppContext
from bot_kalung.ui import theme
from bot_kalung.ui.main_window import MainWindow

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


app = QApplication.instance() or QApplication([])

# ---- tokens ---------------------------------------------------------------
theme.set_theme("light")
check("light is the default", theme.name() == "light")
check("light surface is white", theme.color("surface") == "#ffffff")

theme.set_theme("dark")
check("dark theme selected", theme.name() == "dark")
check("dark window is dark", theme.color("window") == "#0f172a")
check("dark text is light", theme.color("text") == "#e2e8f0")
check("every light token has a dark counterpart",
      set(theme.LIGHT) == set(theme.DARK))
check("banner kinds match across themes",
      set(theme.LIGHT_BANNERS) == set(theme.DARK_BANNERS))

# ---- stylesheet translation ------------------------------------------------
css = "background: white; color: #111827; border: 1px solid #e5e7eb;"
theme.set_theme("light")
check("light leaves stylesheets untouched", theme.style(css) == css)

theme.set_theme("dark")
translated = theme.style(css)
check("dark rewrites the background", "white" not in translated)
check("dark rewrites the text colour", "#111827" not in translated)
check("dark uses its own surface", theme.color("surface") in translated)
check("dark uses its own border", theme.color("border") in translated)
check("exporter colours are left alone",
      "#2563eb" not in theme.style("color: #7c3aed;"))

check("palette follows the theme",
      theme.palette().color(QPalette.ColorRole.Window).name() == "#0f172a")
theme.set_theme("light")
check("palette follows back to light",
      theme.palette().color(QPalette.ColorRole.Window).name() == "#ffffff")

# ---- settings toggle --------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    (root / "AMJ").mkdir(parents=True)

    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set("setup_complete", "1")

    check("theme setting defaults to light", ctx.settings.get("theme") == "light")

    window = MainWindow(ctx)
    view = window.settings
    check("theme selector present", view.theme_combo.count() == 2)
    check("selector loads the stored theme",
          view.theme_combo.currentData() == "light")

    emitted = []
    window.theme_changed.connect(emitted.append)

    index = view.theme_combo.findData("dark")
    view.theme_combo.setCurrentIndex(index)
    view.save()

    check("theme persisted", ctx.settings.get("theme") == "dark")
    check("a rebuild was requested", emitted == ["dark"])

    # Saving again without changing the theme must not ask for a rebuild.
    emitted.clear()
    view.save()
    check("no rebuild when the theme is unchanged", emitted == [])

    # And a second session picks the theme up.
    other = AppContext()
    other.attach(root)
    check("another session sees the saved theme",
          other.settings.get("theme") == "dark")

    view.load()
    check("selector reloads as dark", view.theme_combo.currentData() == "dark")

    # Building a window under the dark theme must not raise.
    theme.apply(app, "dark")
    dark_window = MainWindow(ctx)
    check("window builds under the dark theme", dark_window is not None)
    check("sidebar picked up a dark background",
          theme.color("surface_muted") in dark_window.sidebar.styleSheet())
    dark_window.wizard.shutdown()
    dark_window.settings.shutdown()

    theme.apply(app, "light")
    window.wizard.shutdown()
    view.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Theme OK - all checks passed.")
