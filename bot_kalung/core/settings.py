"""Typed read/write helpers over the `settings` key/value table (PRD 13.3)."""

from __future__ import annotations

import json
from typing import Any

from .constants import DEFAULT_QUARANTINE_COUNTRIES, NTFY_DEFAULT_SERVER
from .db import Database

# Known keys, with their defaults. Complex values are JSON-encoded per PRD 13.3.
DEFAULTS: dict[str, Any] = {
    "setup_complete": "0",
    "google_drive_root": "",
    "llm_provider": "anthropic",          # anthropic | ollama
    "llm_api_key": "",
    "ollama_model": "llama3",
    "ollama_url": "http://localhost:11434",
    "my_email": "",
    "theme": "light",                     # light | dark
    "quarantine_countries": json.dumps(DEFAULT_QUARANTINE_COUNTRIES),
    "exporter_full_names": json.dumps({}),
    # Exporter code -> Drive folder name. Only AMJ matches its code on the real
    # Drive; the rest are mapped in Settings > General.
    "exporter_folders": json.dumps({}),
    # ntfy push. The topic is a fixed constant (constants.NTFY_TOPIC), not a
    # setting; only whether to push and which server are configurable.
    "ntfy_enabled": "1",
    "ntfy_server": NTFY_DEFAULT_SERVER,
}

_JSON_KEYS = {"quarantine_countries", "exporter_full_names", "exporter_folders"}


class Settings:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.query_one("SELECT value FROM settings WHERE key=?", (key,))
        if row is None:
            value = DEFAULTS.get(key, default)
        else:
            value = row["value"]
        if key in _JSON_KEYS and isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return json.loads(DEFAULTS[key])
        return value

    def set(self, key: str, value: Any) -> None:
        if key in _JSON_KEYS or isinstance(value, (list, dict)):
            stored = json.dumps(value)
        elif isinstance(value, bool):
            stored = "1" if value else "0"
        else:
            stored = "" if value is None else str(value)
        self.db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, stored),
        )

    def set_many(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            self.set(key, value)

    def get_bool(self, key: str) -> bool:
        return str(self.get(key, "0")) in ("1", "true", "True")

    # -- convenience ----------------------------------------------------

    @property
    def quarantine_countries(self) -> list[str]:
        return [c.upper() for c in self.get("quarantine_countries")]

    def is_quarantine_country(self, country: str | None) -> bool:
        if not country:
            return False
        return country.strip().upper() in self.quarantine_countries

    @property
    def other_worker_emails(self) -> list[str]:
        """All worker emails except the current user's (PRD Section 7.1)."""
        from .constants import WORKER_EMAILS

        mine = (self.get("my_email") or "").strip().lower()
        return [e for e in WORKER_EMAILS if e.lower() != mine]
