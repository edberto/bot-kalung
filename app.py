"""PyInstaller entry point.

Kept separate from `bot_kalung/main.py` so the frozen executable has a plain
top-level script to start from, and so `python -m bot_kalung.main` keeps working
during development.

Run with `--selftest` to check a build without opening the UI: it imports every
heavy dependency and reports what a frozen bundle actually resolved. That
catches a missing data file or hidden import, which is otherwise only visible
when a worker hits the feature.
"""

import multiprocessing
import sys


def selftest() -> int:
    """Import the runtime dependencies and report. Returns an exit code."""
    checks: list[tuple[str, bool, str]] = []

    def probe(label, fn):
        try:
            checks.append((label, True, str(fn())))
        except Exception as exc:  # noqa: BLE001 - report, don't raise
            checks.append((label, False, f"{type(exc).__name__}: {exc}"))

    probe("PyQt6", lambda: __import__("PyQt6.QtWidgets",
                                     fromlist=["QApplication"]).__name__)
    probe("pdfplumber", lambda: __import__("pdfplumber").__version__)
    probe("pdfminer cmaps", lambda: _pdfminer_data())
    probe("xlwings", lambda: __import__("xlwings").__version__)
    probe("pywin32 COM", lambda: __import__("win32com.client",
                                            fromlist=["Dispatch"]).__name__)
    probe("pywin32 shell", lambda: __import__("win32com.shell",
                                              fromlist=["shell"]).__name__)
    probe("anthropic", lambda: __import__("anthropic").__version__)
    probe("requests", lambda: __import__("requests").__version__)
    probe("certifi CA bundle", lambda: _certifi_bundle())
    probe("app modules", lambda: _app_modules())

    width = max(len(label) for label, _, _ in checks)
    lines = [f"{'PASS' if ok else 'FAIL'}  {label.ljust(width)}  {detail}"
             for label, ok, detail in checks]
    failed = [label for label, ok, _ in checks if not ok]
    lines.append("")
    lines.append(f"{len(failed)} FAILED: {failed}" if failed
                 else "Self-test OK - the build has everything it needs.")

    report = "\n".join(lines)
    print(report)

    # The GUI build has no console, so the report is also written beside the
    # executable — otherwise running --selftest on a frozen build shows nothing.
    try:
        from pathlib import Path

        base = (Path(sys.executable).parent if getattr(sys, "frozen", False)
                else Path.cwd())
        (base / "selftest.log").write_text(report + "\n", encoding="utf-8")
    except OSError:
        pass

    return 1 if failed else 0


def _pdfminer_data() -> str:
    from pathlib import Path

    import pdfminer

    cmap = Path(pdfminer.__file__).parent / "cmap"
    if not cmap.is_dir():
        raise FileNotFoundError(f"cmap directory missing at {cmap}")
    return f"{len(list(cmap.iterdir()))} files"


def _certifi_bundle() -> str:
    from pathlib import Path

    import certifi

    path = Path(certifi.where())
    if not path.is_file():
        raise FileNotFoundError(f"CA bundle missing at {path}")
    return f"{path.stat().st_size // 1024} KB"


def _app_modules() -> str:
    from bot_kalung.core import constants, db, settings  # noqa: F401
    from bot_kalung.services import (  # noqa: F401
        drive, excel, fileops, messaging, naming, printing, shipments, whatsapp,
    )
    from bot_kalung.ui import main_window, theme  # noqa: F401

    return f"{len(constants.WORKFLOW_STEPS)} workflow steps loaded"


def _log_path():
    from pathlib import Path

    base = (Path(sys.executable).parent if getattr(sys, "frozen", False)
            else Path.cwd())
    return base / "botkalung-error.log"


if __name__ == "__main__":
    # Required when frozen: without it a child process would re-run the app.
    multiprocessing.freeze_support()

    if "--selftest" in sys.argv:
        sys.exit(selftest())

    # A frozen GUI build has no console, so an exception at startup would
    # otherwise vanish and the window would simply never appear.
    try:
        from bot_kalung.main import main

        code = main()
    except BaseException:  # noqa: BLE001 - last resort: record and re-raise
        import traceback

        try:
            _log_path().write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        raise
    sys.exit(code)
