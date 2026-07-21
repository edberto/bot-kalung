"""Local bootstrap pointer to the Google Drive root.

PRD Section 13.0 puts the database inside the Drive folder, but the Drive path is
itself a setting stored in that database. That circle is broken with a tiny
machine-local JSON file holding nothing but the path — it is per-machine anyway,
since each worker's Drive syncs to a different local directory.

TODO: confirm with user — the PRD does not specify where the Drive path lives
before the shared DB is reachable. %LOCALAPPDATA%/BotKalung/bootstrap.json is the
conservative choice: no app data is duplicated, only the pointer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


# Tests must never write the real pointer: this file is machine-global, so a
# test that creates a context in a temp folder used to overwrite the worker's
# live Drive path with a directory that is deleted seconds later — the app then
# found no database and re-ran the setup wizard (reported 2026-07-20).
HOME_ENV_VAR = "BOTKALUNG_HOME"


def _bootstrap_file() -> Path:
    override = os.environ.get(HOME_ENV_VAR)
    if override:
        return Path(override) / "bootstrap.json"
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "BotKalung" / "bootstrap.json"


def read_drive_root() -> str | None:
    path = _bootstrap_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    root = data.get("google_drive_root")
    return root if root else None


def write_drive_root(root: str | Path) -> None:
    path = _bootstrap_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"google_drive_root": str(root)}, indent=2),
        encoding="utf-8",
    )


def clear_drive_root() -> None:
    path = _bootstrap_file()
    if path.exists():
        path.unlink()
