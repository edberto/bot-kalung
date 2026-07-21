"""Phase 1 smoke test: schema, pragmas, settings round-trip, template seeding."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.constants import EXPORTERS, STEP_CODES, WORKER_EMAILS
from bot_kalung.core.context import validate_drive_root
from bot_kalung.core.db import Database, db_path_for
from bot_kalung.core.settings import Settings

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "GoogleDrive"
    (root / "AMJ").mkdir(parents=True)
    (root / "1-30").mkdir()

    ok, found = validate_drive_root(root)
    check("drive root validates with an exporter folder", ok and found == ["AMJ"])
    check("drive root rejects a folder with no exporters",
          not validate_drive_root(Path(tmp))[0])

    db = Database(db_path_for(root))
    check("db folder is prefixed to sort last",
          db.path.parent.name == "zzz JANGAN DISENTUH")
    check("db folder still carries the deterrent name",
          "JANGAN DISENTUH" in db.path.parent.name)
    check("is_configured() False before init", not db.is_configured())

    db.initialize()
    check("db file created", db.path.exists())

    # Windows-only; skipped elsewhere.
    if sys.platform == "win32":
        import ctypes

        attributes = ctypes.windll.kernel32.GetFileAttributesW(str(db.path.parent))
        check("db folder is marked hidden", attributes != -1 and attributes & 0x02)
    else:
        print("SKIP  hidden-attribute check (not Windows)")

    conn = db.connect()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()

    check("journal_mode is WAL", mode.lower() == "wal")
    check("busy_timeout is 3000", timeout == 3000)
    # 4 tables, not the PRD's 5: contacts are not configured, so that table
    # was removed with the rest of the contact book (DECISIONS.md 13).
    check("all 4 tables exist", {"shipments", "workflow_steps", "settings",
                                 "message_templates"} <= tables)
    check("contacts table removed", "contacts" not in tables)

    n = db.query_one("SELECT COUNT(*) c FROM message_templates")["c"]
    check("16 templates seeded", n == 16)
    db.seed_templates()
    check("re-seeding is idempotent",
          db.query_one("SELECT COUNT(*) c FROM message_templates")["c"] == 16)

    s = Settings(db)
    check("default provider is anthropic", s.get("llm_provider") == "anthropic")
    check("default quarantine list is PAKISTAN", s.quarantine_countries == ["PAKISTAN"])
    check("quarantine match is case-insensitive", s.is_quarantine_country("pakistan"))
    check("non-quarantine country is False", not s.is_quarantine_country("Singapore"))

    s.set("my_email", WORKER_EMAILS[0])
    check("settings round-trip", s.get("my_email") == WORKER_EMAILS[0])
    check("other_worker_emails excludes mine",
          s.other_worker_emails == WORKER_EMAILS[1:])

    s.set("quarantine_countries", ["PAKISTAN", "INDIA"])
    check("json setting round-trip", s.quarantine_countries == ["PAKISTAN", "INDIA"])

    check("not configured until flag set", not db.is_configured())
    s.set("setup_complete", "1")
    check("is_configured() True after flag", db.is_configured())

    # 4, not the PRD's 6: TASHA merged into TTJ, INDO out of scope (2026-07-20).
    check("4 exporters defined", len(EXPORTERS) == 4)
    check("22 workflow steps defined", len(STEP_CODES) == 22)
    check("step codes unique", len(set(STEP_CODES)) == len(STEP_CODES))

# ---- the bootstrap pointer is machine-global; tests must not write it -------
# Regression for 2026-07-20: a test creating a context in a temp folder
# repointed the installed app at a directory that was deleted moments later,
# so the next launch re-ran the setup wizard.
import os

from bot_kalung.core import bootstrap

real_home = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "BotKalung"
check("tests are redirected away from the real bootstrap file",
      bootstrap._bootstrap_file().parent != real_home)
check("the redirect points at the sandbox",
      bootstrap._bootstrap_file().parent == Path(sandbox.BOOTSTRAP_HOME))

before = (real_home / "bootstrap.json")
stamp = before.stat().st_mtime if before.exists() else None
bootstrap.write_drive_root(Path(sandbox.BOOTSTRAP_HOME) / "fake")
check("writing a root does not touch the real pointer",
      (before.stat().st_mtime if before.exists() else None) == stamp)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Phase 1 OK - all checks passed.")
