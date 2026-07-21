"""Open WhatsApp Web, reusing an existing window when one is already open.

Browsers do not expose tab control to outside programs, so a specific tab cannot
be selected. What is possible on Windows is to find a top-level window whose
title mentions WhatsApp and bring it to the front — which covers the WhatsApp
Desktop app, and a browser window whose *active* tab is WhatsApp Web.

If WhatsApp is sitting in a background tab, its window title reflects whichever
tab is active instead, so it cannot be found; a new tab is opened in that case.
"""

from __future__ import annotations

import ctypes
import webbrowser
from ctypes import wintypes

WHATSAPP_WEB = "https://web.whatsapp.com/"

# Titles that mean a WhatsApp window: the desktop app is just "WhatsApp",
# a browser tab reads e.g. "WhatsApp — Google Chrome" or "(3) WhatsApp - ...".
_TITLE_MARKER = "whatsapp"

SW_RESTORE = 9


def _find_whatsapp_window() -> int | None:
    """Handle of a visible top-level window whose title mentions WhatsApp."""
    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return None  # not Windows

    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if _TITLE_MARKER in buffer.value.lower():
            found.append(hwnd)
            return False  # stop enumerating
        return True

    try:
        user32.EnumWindows(callback, 0)
    except OSError:
        return None
    return found[0] if found else None


def _focus(hwnd: int) -> bool:
    try:
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, SW_RESTORE)
        return bool(user32.SetForegroundWindow(hwnd))
    except (AttributeError, OSError):
        return False


def open_whatsapp() -> tuple[bool, str]:
    """Focus an open WhatsApp window, else open WhatsApp Web in the browser.

    Returns (success, Indonesian message describing what happened).
    """
    handle = _find_whatsapp_window()
    if handle is not None and _focus(handle):
        return True, "WhatsApp dialihkan ke depan."

    try:
        if webbrowser.open(WHATSAPP_WEB):
            return True, "WhatsApp Web dibuka di tab baru."
    except webbrowser.Error:
        pass
    return False, "Tidak dapat membuka WhatsApp Web."
