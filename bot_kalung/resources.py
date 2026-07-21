"""Locating bundled files at runtime.

PyInstaller unpacks a one-file build into a temporary directory and points
`sys._MEIPASS` at it, so a path relative to the source tree does not resolve in
the frozen app. Everything that loads an asset goes through here.
"""

from __future__ import annotations

import sys
from pathlib import Path


def base_path() -> Path:
    """Root the bundled files live under, frozen or not."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent


def asset(name: str) -> Path:
    return base_path() / "bot_kalung" / "assets" / name if _in_bundle() \
        else base_path() / "assets" / name


def _in_bundle() -> bool:
    return getattr(sys, "_MEIPASS", None) is not None


def icon_path() -> Path | None:
    """The .ico, or None when it has not been generated yet."""
    path = asset("icon.ico")
    return path if path.is_file() else None
