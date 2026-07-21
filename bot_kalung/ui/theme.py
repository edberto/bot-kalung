"""Light and dark palettes (PRD Section 11, Tab 1).

Views use a shared token table rather than literal hex, so a theme change moves
every surface at once instead of leaving half the app light.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette

LIGHT = {
    "window": "#ffffff",
    "surface": "#ffffff",
    "surface_muted": "#f9fafb",
    "border": "#e5e7eb",
    "border_strong": "#d1d5db",
    "text": "#111827",
    "text_muted": "#6b7280",
    "text_faint": "#9ca3af",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_soft": "#eef2ff",
    "selection": "#e0e7ff",
    "danger": "#dc2626",
    "success": "#16a34a",
    "warning": "#ea580c",
    "track": "#f3f4f6",
    "row_done": "#f6fef9",
    "row_overdue": "#fffbf5",
    "disabled": "#cbd5e1",
}

DARK = {
    "window": "#0f172a",
    "surface": "#1e293b",
    "surface_muted": "#111c30",
    "border": "#334155",
    "border_strong": "#475569",
    "text": "#e2e8f0",
    "text_muted": "#94a3b8",
    "text_faint": "#64748b",
    "accent": "#3b82f6",
    "accent_hover": "#60a5fa",
    "accent_soft": "#1e3a5f",
    "selection": "#1e3a5f",
    "danger": "#f87171",
    "success": "#4ade80",
    "warning": "#fb923c",
    "track": "#334155",
    "row_done": "#132a1f",
    "row_overdue": "#2a1e12",
    "disabled": "#475569",
}

# Banner colours: (foreground, background, border) per theme.
LIGHT_BANNERS = {
    "success": ("#065f46", "#ecfdf5", "#a7f3d0"),
    "error": ("#991b1b", "#fef2f2", "#fecaca"),
    "warning": ("#92400e", "#fffbeb", "#fde68a"),
    "info": ("#1e40af", "#eff6ff", "#bfdbfe"),
}
DARK_BANNERS = {
    "success": ("#6ee7b7", "#052e21", "#065f46"),
    "error": ("#fca5a5", "#3b1113", "#7f1d1d"),
    "warning": ("#fcd34d", "#3a2a06", "#92400e"),
    "info": ("#93c5fd", "#0f2547", "#1e40af"),
}

_current = dict(LIGHT)
_current_banners = dict(LIGHT_BANNERS)
_current_name = "light"


def set_theme(name: str) -> None:
    global _current, _current_banners, _current_name
    dark = str(name).lower() == "dark"
    _current = dict(DARK if dark else LIGHT)
    _current_banners = dict(DARK_BANNERS if dark else LIGHT_BANNERS)
    _current_name = "dark" if dark else "light"


def name() -> str:
    return _current_name


def color(token: str) -> str:
    return _current.get(token, "#000000")


def banner(kind: str) -> tuple[str, str, str]:
    return _current_banners.get(kind, _current_banners["info"])


# Views are written with the light palette's literal hex. Rather than rewrite
# every stylesheet, they are piped through `style()`, which swaps each known
# light colour for the current theme's equivalent. A colour not listed here
# (exporter badge colours, for instance) is deliberately left alone.
_TOKEN_FOR_LIGHT_HEX = {
    "#ffffff": "surface",
    "white": "surface",
    "#f9fafb": "surface_muted",
    "#fafafa": "surface_muted",
    "#f3f4f6": "track",
    "#e5e7eb": "border",
    "#d1d5db": "border_strong",
    "#111827": "text",
    "#374151": "text",
    "#1f2937": "text",
    "#6b7280": "text_muted",
    "#9ca3af": "text_faint",
    "#2563eb": "accent",
    "#1d4ed8": "accent_hover",
    "#eef2ff": "accent_soft",
    "#e0e7ff": "selection",
    "#dc2626": "danger",
    "#16a34a": "success",
    "#ea580c": "warning",
    "#f6fef9": "row_done",
    "#fffbf5": "row_overdue",
    "#cbd5e1": "disabled",
    "#f8fafc": "surface",
    "#f5f7ff": "accent_soft",
}


def style(css: str) -> str:
    """Translate a light-theme stylesheet into the current theme."""
    if _current_name == "light":
        return css
    out = css
    for literal, token in _TOKEN_FOR_LIGHT_HEX.items():
        out = out.replace(literal, _current[token])
    return out


def palette() -> QPalette:
    """Qt palette for the current theme, covering the un-styled widgets."""
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(color("window")))
    p.setColor(QPalette.ColorRole.WindowText, QColor(color("text")))
    p.setColor(QPalette.ColorRole.Base, QColor(color("surface")))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(color("surface_muted")))
    p.setColor(QPalette.ColorRole.Text, QColor(color("text")))
    p.setColor(QPalette.ColorRole.Button, QColor(color("surface")))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(color("text")))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(color("surface")))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(color("text")))
    p.setColor(QPalette.ColorRole.Highlight, QColor(color("accent")))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(color("text_faint")))
    return p


def apply(qt_app, name_: str = "light") -> None:
    """Set the theme and push its palette onto the running application."""
    set_theme(name_)
    qt_app.setStyle("Fusion")
    qt_app.setPalette(palette())
