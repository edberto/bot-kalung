"""Small shared widgets. Kept style-only so views stay readable."""

from __future__ import annotations

from . import theme

from datetime import date

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QLabel, QLayout, QPushButton, QSpinBox, QWidget,
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


class FlowLayout(QLayout):
    """A layout that wraps its items onto the next line when a row runs out of
    horizontal room (adapted from Qt's flow-layout example).

    Used where a variable number of small chips must stay inside a narrow,
    fixed-width card instead of spilling off its right edge.
    """

    def __init__(self, parent=None, spacing=5):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        return size + QSize(left + right, top + bottom)

    def _do_layout(self, rect, test_only):
        left, top, right, bottom = self.getContentsMargins()
        effective = rect.adjusted(left, top, -right, -bottom)
        x, y, line_height = effective.x(), effective.y(), 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + bottom


# Shared styling for the shipment-detail sections, so the four panels read as
# clearly separated, prominently-titled cards.
CARD_STYLE = ("background: #ffffff; border: 1px solid #e5e7eb;"
              "border-radius: 10px;")
SECTION_HEADER_STYLE = ("border: none; background: transparent; font-size: 16px;"
                        "font-weight: 800; color: #0f172a;")


def section_header(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(theme.style(SECTION_HEADER_STYLE))
    return label


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
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Built from theme tokens (not literal hex) so the fill AND the text are
        # both correct in dark mode — a hardcoded light fill left the text
        # invisible there. A solid muted fill keeps it visible on a white card.
        self.setStyleSheet(f"""
            QPushButton {{
                background: {theme.color('track')}; color: {theme.color('text')};
                border: 1px solid {theme.color('border_strong')};
                border-radius: 6px; padding: 8px 16px; font-weight: 600;
            }}
            QPushButton:hover:enabled {{ background: {theme.color('accent_soft')};
                border-color: {theme.color('accent')};
                color: {theme.color('accent')}; }}
            QPushButton:pressed:enabled {{ background: {theme.color('selection')}; }}
            QPushButton:disabled {{ background: {theme.color('surface_muted')};
                color: {theme.color('text_faint')};
                border-color: {theme.color('border')}; }}
        """)


class DangerButton(QPushButton):
    """For destructive actions (delete). Red so it never reads as routine."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setMinimumHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # The error banner triple is theme-aware, so the red pill adapts to dark
        # mode instead of staying a light chip with invisible text.
        fg, bg, border = theme.banner("error")
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; color: {fg}; border: 1px solid {border};
                border-radius: 6px; padding: 8px 16px; font-weight: 600;
            }}
            QPushButton:hover:enabled {{ background: {border}; }}
            QPushButton:pressed:enabled {{ background: {border}; }}
            QPushButton:disabled {{ background: {theme.color('surface_muted')};
                color: {theme.color('text_faint')};
                border-color: {theme.color('border')}; }}
        """)


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
    """Dismissable banner for inline feedback (PRD Sections 3, 5, 14).

    A "✕" in the top-right clears it; the banner also still clears whenever a
    view calls clear() or shows a new message.
    """

    def __init__(self):
        super().__init__()
        self.setWordWrap(True)
        self.setVisible(False)

        # A plain "×"; NOT a flat button — a flat QPushButton on Windows draws
        # through the native style and ignores the stylesheet text colour, so
        # the glyph rendered invisibly.
        self._close = QPushButton("×", self)
        self._close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close.setFixedSize(20, 20)
        self._close.setToolTip("Tutup")
        self._close.clicked.connect(self.clear)
        self._close.hide()

    def _show(self, kind: str, text: str):
        # Banner colours come from the theme, not the generic hex translation:
        # a light banner tint on a dark surface reads as a bright card.
        fg, bg, border = theme.banner(kind)
        self.setStyleSheet(
            f"color: {fg}; background: {bg}; border: 1px solid {border};"
            # Leave room on the right so the text never runs under the ✕.
            "border-radius: 6px; padding: 10px 30px 10px 12px;")
        self._close.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; color: {fg};"
            " font-size: 17px; font-weight: 700; padding: 0; }"
            f"QPushButton:hover {{ color: {fg}; background: {border};"
            " border-radius: 4px; }}")
        self.setText(text)
        self.setVisible(True)
        self._close.show()
        self._position_close()

    def _position_close(self):
        self._close.move(self.width() - self._close.width() - 6, 6)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_close()

    def show_success(self, text: str):
        self._show("success", text)

    def show_error(self, text: str):
        self._show("error", text)

    def show_warning(self, text: str):
        self._show("warning", text)

    def show_info(self, text: str):
        self._show("info", text)

    def clear(self):
        self._close.hide()
        self.setText("")
        self.setVisible(False)
