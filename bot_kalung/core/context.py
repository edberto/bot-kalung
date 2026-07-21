"""Application context — resolves the Drive root, opens the DB, exposes Settings."""

from __future__ import annotations

from pathlib import Path

from . import bootstrap
from .constants import EXPORTERS
from .db import Database, db_path_for
from .settings import Settings


def validate_drive_root(root: str | Path) -> tuple[bool, list[str]]:
    """PRD Section 3 Step 1: the root must contain >= 1 exporter subfolder.

    Returns (is_valid, list of exporter folder names found).
    """
    path = Path(root)
    if not path.is_dir():
        return False, []
    found = [e for e in EXPORTERS if (path / e).is_dir()]
    return bool(found), found


class AppContext:
    """Holds the live DB + Settings for the session."""

    def __init__(self):
        self.drive_root: str | None = None
        self.db: Database | None = None
        self.settings: Settings | None = None

    def load(self) -> bool:
        """Attach to an existing configured install. False => run setup wizard."""
        root = bootstrap.read_drive_root()
        if not root or not Path(root).is_dir():
            return False
        db = Database(db_path_for(root))
        # PRD 13.0: a missing folder/file means treat as first-time setup.
        if not db.path.exists() or not db.is_configured():
            return False
        self.attach(root, db)
        return True

    def attach(self, root: str | Path, db: Database | None = None) -> None:
        self.drive_root = str(root)
        self.db = db or Database(db_path_for(root))
        self.settings = Settings(self.db)

    def create(self, root: str | Path) -> None:
        """Initialize a fresh install at the given Drive root."""
        db = Database(db_path_for(root))
        db.initialize()
        bootstrap.write_drive_root(root)
        self.attach(root, db)
        self.settings.set("google_drive_root", str(root))
