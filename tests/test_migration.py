"""Schema migration and the crash it prevents.

Reproduces the 2026-07-20 report: creating a shipment made the app exit with no
record saved. The database had been created by an older build, so it still had
`shipping_company_id` and no `shipping_company`; the INSERT failed inside a Qt
slot, and PyQt aborts the process when an exception escapes a slot.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.db import Database, _expected_columns, db_path_for
from bot_kalung.services.shipments import Shipments

# The schema exactly as an earlier build wrote it.
OLD_SCHEMA = """
CREATE TABLE shipments (
    id TEXT PRIMARY KEY, exporter_code TEXT NOT NULL,
    sequence_number INTEGER NOT NULL, booking_number TEXT, vessel_name TEXT,
    voyage TEXT, etd_belawan TEXT, destination_port TEXT,
    destination_country TEXT, container_quantity INTEGER,
    container_size_short TEXT, empty_pickup_location TEXT,
    quarantine_required INTEGER NOT NULL DEFAULT 0, folder_path TEXT,
    do_pdf_filename TEXT, shipping_company_id TEXT,
    status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE workflow_steps (
    id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL, step_code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', completed_at TEXT,
    completion_source TEXT, UNIQUE (shipment_id, step_code)
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE contacts (
    id TEXT PRIMARY KEY, role TEXT NOT NULL, name TEXT, email TEXT,
    whatsapp TEXT, exporter_code TEXT, shipping_company_name TEXT
);
CREATE TABLE message_templates (
    id TEXT PRIMARY KEY, step_code TEXT NOT NULL, channel TEXT NOT NULL,
    recipient_role TEXT, subject_template TEXT, body_template TEXT
);
"""

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def columns(path, table="shipments"):
    conn = sqlite3.connect(str(path))
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def table_names(path):
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


# ---- schema parsing --------------------------------------------------------
expected = _expected_columns()
check("all seven tables parsed from SCHEMA",
      set(expected) == {"shipments", "workflow_steps", "settings",
                        "message_templates", "bnct_checks", "notifications",
                        "audit_log"})
check("shipments columns parsed", len(expected["shipments"]) == 19)
check("shipping_company is expected", "shipping_company" in expected["shipments"])
check("table constraints are not read as columns",
      "UNIQUE" not in expected["workflow_steps"]
      and "(shipment_id," not in expected["workflow_steps"])

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Drive"
    path = db_path_for(root)
    path.parent.mkdir(parents=True)

    # ---- build a database the way the old version did ----------------------
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO settings (key, value) VALUES ('setup_complete','1')")
    conn.commit()
    conn.close()

    before = columns(path)
    table_names_before = table_names(path)
    check("old database lacks shipping_company", "shipping_company" not in before)
    check("old database has shipping_company_id", "shipping_company_id" in before)

    # ---- the failure, before the fix ---------------------------------------
    probe = sqlite3.connect(str(path))
    try:
        probe.execute(
            "INSERT INTO shipments (id, exporter_code, sequence_number, "
            "shipping_company, created_at) VALUES ('x','AMJ',1,'EVERGREEN','now')")
        check("an unmigrated database rejects the new column", False)
    except sqlite3.OperationalError as exc:
        check("an unmigrated database rejects the new column",
              "shipping_company" in str(exc))
    finally:
        probe.close()   # Windows will not delete a file that is still open

    # ---- migrate -----------------------------------------------------------
    db = Database(path)
    db.initialize()

    after = columns(path)
    check("migration adds the missing column", "shipping_company" in after)
    check("migration keeps the obsolete column rather than dropping data",
          "shipping_company_id" in after)
    check("existing tables are otherwise untouched", before <= after)

    # A brand-new table (bnct_checks, added 2026-07-21) must appear on an
    # existing database, not only on a freshly created one.
    check("the old database had no bnct_checks table",
          "bnct_checks" not in table_names_before)
    check("migration creates the new bnct_checks table",
          "bnct_checks" in table_names(path))
    check("the new table has its key columns",
          {"shipment_id", "loading_remain", "phase"}
          <= columns(path, "bnct_checks"))

    # workflow_steps gained the ad-hoc-step + remark columns (2026-07-21).
    check("migration adds the custom-step / remark columns",
          {"position", "is_custom", "title", "remark", "remark_author",
           "remark_at", "added_by", "added_at",
           "due_date"} <= columns(path, "workflow_steps"))

    # The audit trail table (2026-07-21) must appear too, and must NOT carry a
    # cascading shipment FK — its "deleted" entries have to outlive the shipment.
    check("migration creates the audit_log table",
          "audit_log" in table_names(path))
    check("audit_log has its columns",
          {"actor", "action", "shipment_id", "label", "detail"}
          <= columns(path, "audit_log"))

    # ---- the operation that used to kill the app ---------------------------
    shipments = Shipments(db)
    shipment_id = shipments.create({
        "exporter_code": "AMJ", "sequence_number": 24,
        "booking_number": "084600048570", "vessel_name": "EVER CONCERT",
        "voyage": "0800-088N", "etd_belawan": "2026-08-03",
        "destination_port": "KARACHI", "destination_country": "PAKISTAN",
        "container_quantity": 2, "container_size_short": "40'",
        "quarantine_required": True, "folder_path": str(root),
        "shipping_company": "EVERGREEN",
    })
    check("shipment is created", shipment_id is not None)
    check("shipment is persisted and active", len(shipments.active()) == 1)
    check("shipping company stored",
          shipments.get(shipment_id)["shipping_company"] == "EVERGREEN")
    check("workflow steps seeded", len(shipments.steps(shipment_id)) == 22)

    # A legacy step row (position NULL, is_custom NULL — as a pre-migration
    # build left it) must still read as a built-in and sort correctly.
    db.execute("UPDATE workflow_steps SET position=NULL, is_custom=NULL "
               "WHERE shipment_id=? AND step_code='A2'", (shipment_id,))
    legacy_steps = shipments.steps(shipment_id)
    check("legacy NULL-position row still ordered as its built-in",
          [s.code for s in legacy_steps][:2] == ["A1", "A2"])
    check("legacy NULL is_custom reads as built-in",
          not next(s for s in legacy_steps if s.code == "A2").is_custom)
    # And ticking it (UPSERT path) persists despite the legacy shape.
    shipments.set_step(shipment_id, "A2", True)
    check("legacy step can still be ticked",
          next(s for s in shipments.steps(shipment_id) if s.code == "A2").is_complete)

    # ---- and it survives a reopen, which is what the report described ------
    reopened = Shipments(Database(path))
    check("still there after reopening the database",
          len(reopened.active()) == 1)
    check("reopened record keeps its fields",
          reopened.get(shipment_id)["booking_number"] == "084600048570")

    # ---- migration is idempotent -------------------------------------------
    db.initialize()
    check("running migration twice is a no-op", columns(path) == after)
    check("no duplicate shipments after re-init", len(shipments.active()) == 1)

    # ---- a fresh database needs no migration -------------------------------
    fresh_root = Path(tmp) / "Fresh"
    fresh = Database(db_path_for(fresh_root))
    fresh.initialize()
    conn = fresh.connect()
    added = fresh._add_missing_columns(conn)
    conn.close()
    check("a current database reports nothing to add", added == [])

    # ---- the real regression: opening (not creating) an old install --------
    # The migration only ever ran in create(); attach()/load() did not call
    # initialize(), so an install configured by an older build never gained
    # new tables and the first query hit "no such table: bnct_checks". Opening
    # such an install through AppContext must now migrate it.
    import os

    from bot_kalung.core import bootstrap
    from bot_kalung.core.context import AppContext

    legacy_root = Path(tmp) / "Legacy"
    legacy_path = db_path_for(legacy_root)
    legacy_path.parent.mkdir(parents=True)
    lc = sqlite3.connect(str(legacy_path))
    lc.executescript(OLD_SCHEMA)   # has shipments/settings/... but no bnct_checks
    lc.execute("INSERT INTO settings (key, value) VALUES ('setup_complete','1')")
    lc.commit()
    lc.close()
    check("legacy install lacks bnct_checks before opening",
          "bnct_checks" not in table_names(legacy_path))

    os.environ["BOTKALUNG_HOME"] = str(Path(tmp) / "home")
    bootstrap.write_drive_root(str(legacy_root))

    ctx = AppContext()
    check("an existing configured install loads", ctx.load())
    check("opening the install created the missing table",
          "bnct_checks" in table_names(legacy_path))
    # The exact query that used to crash the app (monitor.latest / monitored)
    # now works instead of raising "no such table".
    from bot_kalung.services.bnct_monitor import BnctMonitor

    monitor = BnctMonitor(ctx.db)
    check("the BNCT query that crashed now runs",
          monitor.latest("nobody") is None and monitor.monitored() == [])

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Migration OK - all checks passed.")
