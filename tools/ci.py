"""Local CI/CD pipeline for Bot Kalung.

There is no cloud runner: this is a Windows desktop app, and several tests need
Microsoft Excel (COM) and the team's Google Drive mounted at G:\\. So the
pipeline runs on a developer's machine and degrades gracefully where those are
absent (those tests report SKIP, not FAIL).

Stages, in order:
    lint    pyflakes over the package, app.py, tools/ and tests/
    test    every tests/test_*.py, classified PASS / SKIP / FAIL
    schema  schema-migration verification (and optional --migrate of a real DB)
    build   PyInstaller build + the packaged exe's --selftest   [opt-in]

Usage:
    python tools/ci.py                 # lint + test + schema (the CI gate)
    python tools/ci.py --fast          # lint + fast tests + schema (git hook)
    python tools/ci.py --build         # everything, including the exe build
    python tools/ci.py --all           # alias for --build
    python tools/ci.py --migrate PATH  # also apply the schema to a real DB
    python tools/ci.py --only test     # run a single stage

Exit code is non-zero if any stage fails, so it works as a git hook and a
release gate. Run it with the project's venv Python so imports resolve.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

# Tests that drive Excel or read the live Google Drive. They SKIP cleanly when
# those are unavailable, but they are slow, so --fast leaves them out entirely.
EXTERNAL_TESTS = {
    "test_excel_amj", "test_excel_nit", "test_excel_ttj_tsi",
    "test_pdf_export", "test_drive", "test_fileops", "test_resequence",
    "test_etd_change",
}

LINT_TARGETS = ["bot_kalung", "app.py", "tools", "tests"]

RESET, BOLD = "\033[0m", "\033[1m"
GREEN, RED, YELLOW, GREY = "\033[32m", "\033[31m", "\033[33m", "\033[90m"


def _c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


def header(title: str) -> None:
    print(f"\n{BOLD}== {title} =={RESET}")


# -- stages ------------------------------------------------------------------

def stage_lint() -> bool:
    header("lint (pyflakes)")
    result = subprocess.run(
        [PYTHON, "-m", "pyflakes", *LINT_TARGETS],
        cwd=ROOT, capture_output=True, text=True)
    # pyflakes ignores "# noqa"; the codebase uses it for intentional
    # side-effect imports (sandbox, the selftest probes), so honour it here.
    real = [line for line in (result.stdout + result.stderr).splitlines()
            if line.strip() and not _suppressed(line)]
    if not real:
        print(_c("PASS", GREEN), "no lint errors")
        return True
    print(_c("FAIL", RED), "pyflakes reported:")
    print("\n".join(real))
    return False


def _suppressed(pyflakes_line: str) -> bool:
    """True if the source line the warning points at carries a # noqa marker."""
    parts = pyflakes_line.split(":", 2)
    if len(parts) < 2:
        return False
    try:
        source = (ROOT / parts[0]).read_text(encoding="utf-8").splitlines()
        return "# noqa" in source[int(parts[1]) - 1]
    except (OSError, ValueError, IndexError):
        return False


def _classify(returncode: int, output: str) -> str:
    # A failing test exits non-zero (sys.exit(1) after printing "N FAILED").
    # A passing test always prints "... all checks passed" (dash or em-dash).
    # A skipped test exits 0 without that footer. Keying off these avoids
    # false hits like the UI string "Skip link".
    if returncode != 0:
        return "FAIL"
    if "all checks passed" in output.lower():
        return "PASS"
    return "SKIP"


def stage_test(fast: bool) -> bool:
    header("test" + (" (fast — Excel/Drive tests skipped)" if fast else ""))
    files = sorted(p for p in (ROOT / "tests").glob("test_*.py"))
    ok = True
    width = max(len(p.stem) for p in files)
    for path in files:
        name = path.stem
        if fast and name in EXTERNAL_TESTS:
            print(f"  {name.ljust(width)}  {_c('----', GREY)}  (skipped in --fast)")
            continue
        start = time.perf_counter()
        result = subprocess.run(
            [PYTHON, str(path)], cwd=ROOT, capture_output=True, text=True)
        elapsed = time.perf_counter() - start
        verdict = _classify(result.returncode, result.stdout + result.stderr)
        color = {"PASS": GREEN, "SKIP": YELLOW, "FAIL": RED}[verdict]
        print(f"  {name.ljust(width)}  {_c(verdict.ljust(4), color)}  {elapsed:5.1f}s")
        if verdict == "FAIL":
            ok = False
            tail = "\n".join(
                (result.stdout + result.stderr).strip().splitlines()[-8:])
            print(_c(_indent(tail), GREY))
    return ok


def _indent(text: str, prefix: str = "      ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def stage_schema(migrate_path: str | None) -> bool:
    header("schema migration")
    # Import here so a lint/test-only run does not pay for it.
    sys.path.insert(0, str(ROOT))
    import sqlite3
    import tempfile

    from bot_kalung.core.db import Database, _expected_columns, db_path_for

    expected = _expected_columns()
    ok = True

    def report(label: str, passed: bool):
        nonlocal ok
        print(f"  {_c('PASS', GREEN) if passed else _c('FAIL', RED)}  {label}")
        ok = ok and passed

    with tempfile.TemporaryDirectory() as tmp:
        # 1. A fresh database matches the schema the code declares.
        fresh = Database(db_path_for(Path(tmp) / "fresh"))
        fresh.initialize()
        conn = fresh.connect()
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            report("fresh init creates every declared table",
                   set(expected) <= tables)
            drift = {}
            for table, cols in expected.items():
                actual = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                missing = set(cols) - actual
                if missing:
                    drift[table] = missing
            report(f"no column drift between SCHEMA and a fresh DB {drift or ''}",
                   not drift)
        finally:
            conn.close()

        # 2. An old database gains a missing table and a missing column.
        old_path = db_path_for(Path(tmp) / "old")
        old_path.parent.mkdir(parents=True)
        c = sqlite3.connect(str(old_path))
        # A realistic pre-bnct database: shipments carries the columns the
        # schema's indexes reference (status), but lacks shipping_company and
        # the whole bnct_checks table.
        c.executescript(
            "CREATE TABLE shipments (id TEXT PRIMARY KEY, exporter_code TEXT NOT NULL,"
            " sequence_number INTEGER NOT NULL,"
            " status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL);"
            "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);")
        c.commit()
        c.close()
        Database(old_path).initialize()
        c = sqlite3.connect(str(old_path))
        try:
            tabs = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            cols = {r[1] for r in c.execute("PRAGMA table_info(shipments)")}
            report("migration adds a new table (bnct_checks)",
                   "bnct_checks" in tabs)
            report("migration adds a missing column (shipping_company)",
                   "shipping_company" in cols)
        finally:
            c.close()

        # 3. Running it again changes nothing.
        before = _snapshot(old_path)
        Database(old_path).initialize()
        report("migration is idempotent", _snapshot(old_path) == before)

    # 4. Optional: bring a real database up to date, on purpose.
    if migrate_path:
        target = Path(migrate_path)
        if not target.is_file():
            report(f"--migrate target not found: {target}", False)
        else:
            before = _snapshot(target)
            Database(target).initialize()
            after = _snapshot(target)
            added = {t for t in after if t not in before}
            print(f"  {_c('OK', GREEN)}  migrated {target}"
                  + (f" (added tables: {sorted(added)})" if added else " (already current)"))
    return ok


def _snapshot(path) -> dict:
    import sqlite3

    conn = sqlite3.connect(str(path))
    try:
        snap = {}
        for (table,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"):
            snap[table] = sorted(
                r[1] for r in conn.execute(f"PRAGMA table_info({table})"))
        return snap
    finally:
        conn.close()


def _clear_stale_exe(patience: int = 40) -> None:
    """Remove a previous dist exe so PyInstaller can overwrite it. Windows
    Defender briefly locks a freshly-scanned binary, so retry past that.
    """
    import time

    exe = ROOT / "dist" / "Bot Kalung.exe"
    for _ in range(patience):
        if not exe.exists():
            return
        try:
            exe.unlink()
            return
        except OSError:
            time.sleep(1.0)


def stage_build() -> bool:
    import time

    header("build (PyInstaller + selftest)")
    # Defender's lock on the previous exe is transient (it can hold it for tens
    # of seconds while scanning), so a build that fails on "Access is denied" is
    # retried after clearing and waiting.
    attempts = 5
    build = None
    for attempt in range(attempts):
        _clear_stale_exe()
        build = subprocess.run(
            [PYTHON, "-m", "PyInstaller", "bot_kalung.spec", "--noconfirm"],
            cwd=ROOT, capture_output=True, text=True)
        if build.returncode == 0:
            break
        combined = build.stdout + build.stderr
        if attempt < attempts - 1 and ("Access is denied" in combined
                                       or "PermissionError" in combined):
            time.sleep(8)
            continue
        print(_c("FAIL", RED), "PyInstaller build failed:")
        print(_indent("\n".join(combined.splitlines()[-15:])))
        return False
    exe = ROOT / "dist" / "Bot Kalung.exe"
    if not exe.is_file():
        print(_c("FAIL", RED), f"exe not produced at {exe}")
        return False
    print(_c("PASS", GREEN), f"built {exe.name}")

    selftest = subprocess.run(
        [str(exe), "--selftest"], cwd=ROOT, capture_output=True, text=True)
    passed = selftest.returncode == 0 and "Self-test OK" in selftest.stdout
    print(_c("PASS" if passed else "FAIL", GREEN if passed else RED),
          "exe --selftest")
    if not passed:
        print(_indent(selftest.stdout + selftest.stderr))
    return passed


# -- driver ------------------------------------------------------------------

ALL_STAGES = ["lint", "test", "schema", "build"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Bot Kalung local CI/CD.")
    parser.add_argument("--fast", action="store_true",
                        help="skip Excel/Drive tests and the build (git hook use)")
    parser.add_argument("--build", action="store_true",
                        help="also run the PyInstaller build + selftest")
    parser.add_argument("--all", action="store_true", help="alias for --build")
    parser.add_argument("--migrate", metavar="DB",
                        help="apply the current schema to a real database")
    parser.add_argument("--only", choices=ALL_STAGES,
                        help="run just one stage")
    args = parser.parse_args()

    if args.only:
        stages = [args.only]
    else:
        stages = ["lint", "test", "schema"]
        if args.build or args.all:
            stages.append("build")

    print(f"{BOLD}Bot Kalung CI/CD{RESET}  stages: {', '.join(stages)}"
          + ("  (fast)" if args.fast else ""))
    started = time.perf_counter()
    results: dict[str, bool] = {}
    for stage in stages:
        if stage == "lint":
            results[stage] = stage_lint()
        elif stage == "test":
            results[stage] = stage_test(args.fast)
        elif stage == "schema":
            results[stage] = stage_schema(args.migrate)
        elif stage == "build":
            results[stage] = stage_build()

    header("summary")
    for stage in stages:
        passed = results[stage]
        print(f"  {stage.ljust(7)}  {_c('PASS', GREEN) if passed else _c('FAIL', RED)}")
    total = time.perf_counter() - started
    failed = [s for s, ok in results.items() if not ok]
    print(f"\n{BOLD}{'FAILED' if failed else 'OK'}{RESET} in {total:.1f}s"
          + (f" — {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
