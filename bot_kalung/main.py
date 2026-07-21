"""Application entry point.

Launch: python -m bot_kalung.main
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import resources
from .core.constants import APP_NAME
from .core.context import AppContext
from .ui import theme
from .ui.setup_wizard import SetupWizard


def apply_icon(qt: QApplication) -> None:
    """Set the window/taskbar icon, if the asset has been generated."""
    path = resources.icon_path()
    if path is not None:
        qt.setWindowIcon(QIcon(str(path)))


def install_exception_handler() -> None:
    """Show and record unhandled errors instead of dying silently.

    PyQt aborts the process when a Python exception escapes a slot, so a single
    unhandled error made the window vanish with nothing written anywhere — the
    hardest possible failure to diagnose. This keeps the app alive, shows what
    went wrong, and appends the traceback to botkalung-error.log next to the
    executable.
    """
    import traceback

    def handle(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        text = "".join(traceback.format_exception(
            exc_type, exc_value, exc_traceback))
        try:
            base = (Path(sys.executable).parent if getattr(sys, "frozen", False)
                    else Path.cwd())
            with (base / "botkalung-error.log").open("a", encoding="utf-8") as log:
                log.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} ---\n")
                log.write(text)
        except OSError:
            pass

        QMessageBox.critical(
            None, f"{APP_NAME} — kesalahan",
            f"Terjadi kesalahan yang tidak terduga:\n\n"
            f"{exc_type.__name__}: {exc_value}\n\n"
            "Rincian disimpan di botkalung-error.log. Aplikasi tetap berjalan, "
            "tetapi periksa hasil pekerjaan terakhir Anda.")

    sys.excepthook = handle


def _set_windows_app_id() -> None:
    """Give Windows an explicit AppUserModelID.

    Without one, a Python-hosted window is grouped under the interpreter and the
    taskbar shows Python's icon rather than the app's. Harmless elsewhere.
    """
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "BotKalung.ExportManager")
    except (AttributeError, OSError):
        pass


def apply_light_theme(qt: QApplication) -> None:
    """Backwards-compatible helper: apply the light theme."""
    theme.apply(qt, "light")


class Application:
    def __init__(self):
        self.qt = QApplication(sys.argv)
        self.qt.setApplicationName(APP_NAME)
        apply_light_theme(self.qt)
        apply_icon(self.qt)
        _set_windows_app_id()
        install_exception_handler()
        self.ctx = AppContext()
        self.wizard: SetupWizard | None = None
        self.window = None

    def start(self) -> int:
        try:
            configured = self.ctx.load()
        except Exception as exc:  # noqa: BLE001 - must not crash before any UI
            QMessageBox.critical(
                None, APP_NAME,
                "Gagal membuka basis data:\n\n"
                f"{exc}\n\nPengaturan awal akan dijalankan ulang.")
            configured = False

        if configured:
            self._show_main_window()
        else:
            self._show_wizard()
        return self.qt.exec()

    def _show_wizard(self):
        self.wizard = SetupWizard(self.ctx)
        self.wizard.setup_complete.connect(self._on_setup_complete)
        self.wizard.show()

    def _on_setup_complete(self):
        if self.wizard:
            self.wizard.close()
            self.wizard = None
        self._show_main_window()

    def _show_main_window(self):
        from .ui.main_window import MainWindow

        theme.apply(self.qt, self.ctx.settings.get("theme") or "light")
        self.window = MainWindow(self.ctx)
        self.window.theme_changed.connect(self._rebuild_for_theme)
        self.window.show()

    def _rebuild_for_theme(self, name: str):
        """Rebuild the window under the new theme.

        Widget styles are set when a widget is constructed, so rebuilding is
        simpler and more reliable than restyling every widget in place. All
        state lives in the database, so nothing is lost.
        """
        from .ui.main_window import MainWindow

        theme.apply(self.qt, name)
        geometry = self.window.saveGeometry() if self.window else None
        old = self.window
        self.window = MainWindow(self.ctx)
        self.window.theme_changed.connect(self._rebuild_for_theme)
        if geometry is not None:
            self.window.restoreGeometry(geometry)
        self.window.show()
        if old is not None:
            old.close()
            old.deleteLater()


def main() -> int:
    return Application().start()


if __name__ == "__main__":
    sys.exit(main())
