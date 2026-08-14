"""SQLite access layer (PRD Section 13).

The database file lives inside the shared Google Drive folder so all three
workers hit the same file. Every connection is opened with WAL + a 3s busy
timeout as mandated by Section 13.0.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .constants import DB_FILE_NAME, DB_FOLDER_NAME
from .templates import DEFAULT_TEMPLATES

SCHEMA = """
CREATE TABLE IF NOT EXISTS shipments (
    id                    TEXT PRIMARY KEY,
    exporter_code         TEXT NOT NULL,
    sequence_number       INTEGER NOT NULL,
    booking_number        TEXT,
    vessel_name           TEXT,
    voyage                TEXT,
    etd_belawan           TEXT,
    destination_port      TEXT,
    destination_country   TEXT,
    container_quantity    INTEGER,
    container_size_short  TEXT,
    empty_pickup_location TEXT,
    quarantine_required   INTEGER NOT NULL DEFAULT 0,
    folder_path           TEXT,
    do_pdf_filename       TEXT,
    shipping_company      TEXT,
    notes                 TEXT,
    status                TEXT NOT NULL DEFAULT 'active',
    created_at            TEXT NOT NULL,
    completed_at          TEXT
);

CREATE TABLE IF NOT EXISTS workflow_steps (
    id                TEXT PRIMARY KEY,
    shipment_id       TEXT NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    step_code         TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    completed_at      TEXT,
    completion_source TEXT,
    position          REAL,
    is_custom         INTEGER NOT NULL DEFAULT 0,
    title             TEXT,
    remark            TEXT,
    remark_author     TEXT,
    remark_at         TEXT,
    added_by          TEXT,
    added_at          TEXT,
    due_date          TEXT,
    UNIQUE (shipment_id, step_code)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS message_templates (
    id               TEXT PRIMARY KEY,
    step_code        TEXT NOT NULL,
    channel          TEXT NOT NULL,
    recipient_role   TEXT,
    subject_template TEXT,
    body_template    TEXT
);

CREATE TABLE IF NOT EXISTS bnct_checks (
    id               TEXT PRIMARY KEY,
    shipment_id      TEXT NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    checked_at       TEXT NOT NULL,
    found            INTEGER NOT NULL DEFAULT 0,
    phase            TEXT,
    site             TEXT,
    etd              TEXT,
    open_billing     TEXT,
    open_stacking    TEXT,
    clossing         TEXT,
    clossing_reefer  TEXT,
    atb              TEXT,
    berth            TEXT,
    loading_plan     INTEGER,
    loading_actual   INTEGER,
    loading_remain   INTEGER,
    discharge_plan   INTEGER,
    discharge_actual INTEGER,
    discharge_remain INTEGER,
    restow_plan      INTEGER,
    restow_actual    INTEGER,
    restow_remain    INTEGER,
    departing        INTEGER NOT NULL DEFAULT 0,
    note             TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,
    shipment_id TEXT REFERENCES shipments(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT,
    created_at  TEXT NOT NULL,
    read        INTEGER NOT NULL DEFAULT 0
);

-- Shipment lifecycle trail. shipment_id is deliberately NOT a foreign key:
-- a "shipment deleted" entry must survive the shipment it describes, which an
-- ON DELETE CASCADE would erase. label keeps the entry readable afterwards.
CREATE TABLE IF NOT EXISTS audit_log (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    actor       TEXT,
    action      TEXT NOT NULL,
    shipment_id TEXT,
    label       TEXT,
    detail      TEXT
);

-- Vessels monitored on the BNCT portal independently of any shipment (the
-- "Monitor Kapal" screen). No shipment FK. The last_* columns hold the previous
-- reading so transitions can be detected without a full history table, and drive
-- the screen's status display.
CREATE TABLE IF NOT EXISTS monitored_vessels (
    id              TEXT PRIMARY KEY,
    vessel_name     TEXT NOT NULL,
    voyage          TEXT,
    created_at      TEXT NOT NULL,
    last_checked_at TEXT,
    last_found      INTEGER NOT NULL DEFAULT 0,
    last_phase      TEXT,
    last_departing  INTEGER NOT NULL DEFAULT 0,
    last_summary    TEXT,
    last_reading    TEXT,
    state           TEXT
);

-- Registry for the folder-scan tracker (post-refactor). One row per
-- {exporter_code}{sequence} the scan has handled, so delta scans only add newly
-- eligible folders and never re-import. `done` marks a folder that already had
-- its Bill of Lading scan (complete, never imported); `shipment_id` links the
-- ones that were imported, and survives as a tombstone (SET NULL) if the user
-- later deletes the shipment, so it is not re-imported.
CREATE TABLE IF NOT EXISTS scanned_shipments (
    id              TEXT PRIMARY KEY,
    exporter_code   TEXT NOT NULL,
    sequence_number INTEGER NOT NULL,
    folder_path     TEXT,
    done            INTEGER NOT NULL DEFAULT 0,
    shipment_id     TEXT REFERENCES shipments(id) ON DELETE SET NULL,
    imported_at     TEXT NOT NULL,
    UNIQUE (exporter_code, sequence_number)
);

-- Per-shipment action items (folder-scan tracker) — the documents/tasks that
-- replace the fixed A1..E6 workflow_steps. Seeded on import from the shipment's
-- destination + exporter, plus any ad-hoc items; each carries a status rather
-- than a done flag.
CREATE TABLE IF NOT EXISTS action_items (
    id           TEXT PRIMARY KEY,
    shipment_id  TEXT NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    code         TEXT NOT NULL,
    title        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    position     REAL,
    is_custom    INTEGER NOT NULL DEFAULT 0,
    due_date     TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT
);

-- Containers for a scanned shipment, populated from the VGM read and tracked on
-- BNCT (container search). last_* hold the most recent reading so a transition
-- into 51-STACK RECEIVING can be detected without a history table.
CREATE TABLE IF NOT EXISTS containers (
    id                TEXT PRIMARY KEY,
    shipment_id       TEXT NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    container_no      TEXT NOT NULL,
    size              TEXT,
    type              TEXT,
    last_site         TEXT,
    last_status_code  TEXT,
    last_status_text  TEXT,
    last_checked_at   TEXT,
    UNIQUE (shipment_id, container_no)
);
"""

# Indexes are created AFTER _add_missing_columns, because one references a
# column (workflow_steps.position) that an older database gains only during
# migration — creating it inside SCHEMA would fail on that not-yet-added column.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_shipments_status ON shipments(status);
CREATE INDEX IF NOT EXISTS idx_steps_shipment ON workflow_steps(shipment_id);
CREATE INDEX IF NOT EXISTS idx_steps_position ON workflow_steps(shipment_id, position);
CREATE INDEX IF NOT EXISTS idx_bnct_shipment ON bnct_checks(shipment_id);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_steps_due ON workflow_steps(due_date);
CREATE INDEX IF NOT EXISTS idx_monitored_created ON monitored_vessels(created_at);
CREATE INDEX IF NOT EXISTS idx_scanned_shipment ON scanned_shipments(shipment_id);
CREATE INDEX IF NOT EXISTS idx_action_items_shipment ON action_items(shipment_id);
CREATE INDEX IF NOT EXISTS idx_action_items_due ON action_items(due_date);
CREATE INDEX IF NOT EXISTS idx_containers_shipment ON containers(shipment_id);
"""


class DatabaseBusy(Exception):
    """Raised when SQLite stays locked past the busy timeout (PRD 13.0)."""


def _expected_columns() -> dict[str, dict[str, str]]:
    """Column name -> declaration, per table, parsed from SCHEMA.

    Parsed rather than hand-listed so the migration can never drift from the
    schema it is meant to enforce.
    """
    tables: dict[str, dict[str, str]] = {}
    for match in re.finditer(
            r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", SCHEMA, re.S):
        name, body = match.group(1), match.group(2)
        columns: dict[str, str] = {}
        for line in body.split("\n"):
            line = line.strip().rstrip(",")
            if not line or line.upper().startswith(("UNIQUE", "PRIMARY KEY",
                                                    "FOREIGN KEY", "CHECK")):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                columns[parts[0]] = parts[1]
        tables[name] = columns
    return tables


def db_path_for(drive_root: str | Path) -> Path:
    """Resolve the shared DB path under the Google Drive root."""
    return Path(drive_root) / DB_FOLDER_NAME / DB_FILE_NAME


def hide_folder(folder: str | Path) -> bool:
    """Mark a folder hidden on Windows. Best effort — never raises.

    Keeps the database out of sight in Explorer without preventing the app, or
    Google Drive's sync, from reading it.
    """
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x02
        result = ctypes.windll.kernel32.SetFileAttributesW(
            str(folder), FILE_ATTRIBUTE_HIDDEN)
        return bool(result)
    except (AttributeError, OSError):
        return False  # not Windows, or the call is unavailable


def new_id() -> str:
    return str(uuid.uuid4())


class Database:
    """Thin wrapper that guarantees the PRD-mandated pragmas on every connect."""

    def __init__(self, path: Path):
        self.path = Path(path)

    # -- connection -----------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=3.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def cursor(self, write: bool = False):
        """Yield a cursor; commits on clean exit when `write` is True."""
        conn = self.connect()
        try:
            yield conn.cursor()
            if write:
                conn.commit()
        except sqlite3.OperationalError as exc:
            conn.rollback()
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise DatabaseBusy(str(exc)) from exc
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- lifecycle ------------------------------------------------------

    def initialize(self) -> None:
        """Create the folder, file, schema, and seed rows. Idempotent."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        hide_folder(self.path.parent)
        conn = self.connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
            self._add_missing_columns(conn)
            # Indexes last: idx_steps_position references a column older
            # databases only gain in the migration above.
            conn.executescript(INDEXES)
            conn.commit()
        finally:
            conn.close()
        self.seed_templates()

    def _add_missing_columns(self, conn: sqlite3.Connection) -> list[str]:
        """Bring an existing database up to the current schema.

        `CREATE TABLE IF NOT EXISTS` leaves an existing table exactly as it was,
        so a database made by an older build keeps its old columns forever. A
        write naming a new column then fails at runtime — and because three
        workers share one file over Drive, one of them running an older build is
        enough to create that situation.

        Only additive changes are applied, which is all SQLite's ALTER supports
        and all that has been needed so far. Columns no longer used are left in
        place rather than dropped: they cost nothing and dropping them would
        destroy data.
        """
        added: list[str] = []
        for table, columns in _expected_columns().items():
            existing = {row[1] for row in
                        conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue  # table absent entirely; the schema script made it
            for name, declaration in columns.items():
                if name in existing:
                    continue
                # NOT NULL/DEFAULT clauses are dropped: SQLite cannot add a
                # NOT NULL column without a default, and existing rows have no
                # value for it.
                kind = declaration.split()[0]
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
                added.append(f"{table}.{name}")
        if added:
            conn.commit()
        return added

    def seed_templates(self) -> None:
        """Insert missing templates and keep their step wiring current.

        `step_code`, `channel` and `recipient_role` describe how a template is
        wired into the workflow, so they are refreshed on every launch —
        otherwise a database made before the steps were reordered keeps pointing
        at the old ones. `subject_template` is filled in when it is empty or when
        it still holds a superseded default (so a changed default reaches
        existing installs), but a subject genuinely edited in Settings survives.
        """
        from .templates import SUPERSEDED_SUBJECTS

        placeholders = ",".join("?" for _ in SUPERSEDED_SUBJECTS)
        with self.cursor(write=True) as cur:
            for tid, step, channel, role, subject, body in DEFAULT_TEMPLATES:
                cur.execute(
                    "INSERT OR IGNORE INTO message_templates "
                    "(id, step_code, channel, recipient_role, subject_template, body_template) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (tid, step, channel, role, subject, body),
                )
                cur.execute(
                    "UPDATE message_templates SET step_code=?, channel=?, "
                    "recipient_role=? WHERE id=?",
                    (step, channel, role, tid),
                )
                cur.execute(
                    "UPDATE message_templates SET subject_template=? "
                    "WHERE id=? AND (subject_template IS NULL "
                    f"                OR TRIM(subject_template)=''"
                    f"                OR subject_template IN ({placeholders}))",
                    (subject, tid, *SUPERSEDED_SUBJECTS),
                )

    def is_configured(self) -> bool:
        """First-launch detection (PRD Section 3): setup_complete flag present."""
        if not self.path.exists():
            return False
        try:
            with self.cursor() as cur:
                cur.execute("SELECT value FROM settings WHERE key='setup_complete'")
                row = cur.fetchone()
                return bool(row and row["value"] == "1")
        except sqlite3.DatabaseError:
            return False

    # -- queries used from Phase 2 onward -------------------------------

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self.cursor(write=True) as cur:
            cur.execute(sql, params)
