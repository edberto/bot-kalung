"""Small shared widgets. Kept style-only so views stay readable."""

from __future__ import annotations

from . import theme

from datetime import date

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QLabel, QPushButton, QSpinBox, QWidget,
)

MONTHS_ID = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
             "Agustus", "September", "Oktober", "November", "Desember"]


def format_date_id(iso: str | None) -> str:
    """ISO date -> "3 Agustus 2026". Unparseable input is passed through."""
    if not iso:
        return "-"
    try:
        parsed = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return f"{parsed.day} {MONTHS_ID[parsed.month - 1]} {parsed.year}"


def days_until(iso: str | None) -> int | None:
    """Days from today until an ISO date; negative once past. None if unparseable."""
    if not iso:
        return None
    try:
        return (date.fromisoformat(iso[:10]) - date.today()).days
    except ValueError:
        return None


class Panel(QWidget):
    """A QWidget that actually paints its stylesheet background.

    Plain QWidget subclasses ignore `background`/`border` from a stylesheet
    unless WA_StyledBackground is set — without it the sidebar and cards render
    with the OS window colour, which turns them black under Windows dark mode.
    """

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


class PrimaryButton(QPushButton):
    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setMinimumHeight(36)
        self.setStyleSheet(theme.style("""
            QPushButton {
                background: #2563eb; color: white; border: none;
                border-radius: 6px; padding: 8px 20px; font-weight: 600;
            }
            QPushButton:hover:enabled { background: #1d4ed8; }
            QPushButton:disabled { background: #cbd5e1; color: #f8fafc; }
        """))


class SecondaryButton(QPushButton):
    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setMinimumHeight(36)
        self.setStyleSheet(theme.style("""
            QPushButton {
                background: white; color: #1f2937; border: 1px solid #d1d5db;
                border-radius: 6px; padding: 8px 16px;
            }
            QPushButton:hover:enabled { background: #f3f4f6; }
            QPushButton:disabled { color: #9ca3af; }
        """))


class DangerButton(QPushButton):
    """For destructive actions (delete). Red so it never reads as routine."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setMinimumHeight(36)
        self.setStyleSheet(theme.style("""
            QPushButton {
                background: white; color: #b91c1c; border: 1px solid #fca5a5;
                border-radius: 6px; padding: 8px 16px;
            }
            QPushButton:hover:enabled { background: #fef2f2; border-color: #f87171; }
            QPushButton:disabled { color: #9ca3af; border-color: #e5e7eb; }
        """))


class _NoWheelMixin:
    """Ignore the scroll wheel so a value never changes by accident.

    Scrolling a form that contains spinboxes or dropdowns otherwise silently
    edits whichever one happens to sit under the cursor — a container count or
    sequence number can change without the worker noticing.
    """

    def wheelEvent(self, event):
        event.ignore()


class ComboBox(_NoWheelMixin, QComboBox):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(34)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


class SpinBox(_NoWheelMixin, QSpinBox):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(30)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


class DateEdit(_NoWheelMixin, QDateEdit):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(30)
        self.setCalendarPopup(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


class InlineMessage(QLabel):
    """Banner for inline validation feedback (PRD Sections 3, 5, 14)."""

    def __init__(self):
        super().__init__()
        self.setWordWrap(True)
        self.setVisible(False)

    def _show(self, kind: str, text: str):
        # Banner colours come from the theme, not the generic hex translation:
        # a light banner tint on a dark surface reads as a bright card.
        fg, bg, border = theme.banner(kind)
        self.setStyleSheet(
            f"color: {fg}; background: {bg}; border: 1px solid {border};"
            "border-radius: 6px; padding: 10px 12px;")
        self.setText(text)
        self.setVisible(True)

    def show_success(self, text: str):
        self._show("success", text)

    def show_error(self, text: str):
        self._show("error", text)

    def show_warning(self, text: str):
        self._show("warning", text)

    def show_info(self, text: str):
        self._show("info", text)

    def clear(self):
        self.setText("")
        self.setVisible(False)
