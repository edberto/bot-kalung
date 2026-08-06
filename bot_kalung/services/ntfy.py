"""ntfy push notifications — an additional delivery channel on top of the
in-app tray/badge/screen.

Every notification is also POSTed to a fixed ntfy topic shared by the whole team
(`constants.NTFY_TOPIC`), so anyone subscribed in the ntfy phone app gets the
alert. Public topic, no auth. Fire-and-forget: a failed push must never disturb
a poll, so every error is swallowed and reported only as a False return.
"""

from __future__ import annotations

from ..core.constants import NTFY_DEFAULT_SERVER, NTFY_TOPIC

# ntfy Priority header per notification kind (5 = max urgency, for the LOLO alert).
_PRIORITY = {"schedule": "3", "alongside": "4", "departing": "5"}
_TAGS = {"schedule": "calendar", "alongside": "anchor", "departing": "warning"}


def subscribe_url(settings) -> str:
    """The full URL to subscribe to in the ntfy app (shown in Settings)."""
    server = (settings.get("ntfy_server") or NTFY_DEFAULT_SERVER).rstrip("/")
    return f"{server}/{NTFY_TOPIC}"


def publish(settings, title: str, body: str, *, kind: str = "schedule") -> bool:
    """POST one notification to ntfy. Returns True on a 2xx response.

    Returns False (never raises) when disabled or on any network/HTTP error.
    """
    try:
        if not settings.get_bool("ntfy_enabled"):
            return False
        import requests

        response = requests.post(
            subscribe_url(settings),
            data=(body or "").encode("utf-8"),
            headers={
                "Title": _header_safe(title),
                "Priority": _PRIORITY.get(kind, "3"),
                "Tags": _TAGS.get(kind, "bell"),
            },
            timeout=10)
        return response.ok
    except Exception:      # noqa: BLE001 - a failed push must never disturb a poll
        return False


def _header_safe(text: str) -> str:
    """HTTP headers are latin-1 only; the body carries full UTF-8, so a title
    with a stray non-ASCII glyph is downgraded rather than crashing the POST."""
    return (text or "").encode("ascii", "replace").decode("ascii")
