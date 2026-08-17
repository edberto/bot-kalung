"""Headless server worker — the PC-free ingest + BNCT monitoring.

Runs the Drive-API folder scan and the BNCT monitoring poll on a schedule,
writing to Supabase Postgres. No desktop, no Excel, no G: mount — deployable to a
small Linux host. Notifications are persisted to the DB (the PWA reads them).

Config (env vars; a local secrets/*.env is loaded as a dev fallback):
  SUPABASE_DB_URL         Postgres connection (use the Supabase Session-pooler URI)
  DRIVE_CREDENTIALS       path to the Google service-account JSON
  SCAN_INTERVAL_MINUTES   folder-scan cadence (default 30)
  POLL_INTERVAL_MINUTES   BNCT-poll cadence (default 5)

Run:  python -m bot_kalung.server          # loop forever
      python -m bot_kalung.server --once   # one scan + poll, then exit (cron)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from .core.pg import PostgresDatabase
from .services import bnct, drive_api, tracker
from .services.bnct_monitor import BnctMonitor, build_reading
from .services.containers import Containers
from .services.notifications import NotificationStore
from .services.vessel_monitor import VesselMonitor

log = logging.getLogger("bot_kalung.server")

_DEV_CREDS = "secrets/bot-kalung-7861b7295b7d.json"


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into the environment (does not override real env)."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _config() -> dict:
    _load_env_file(Path("secrets/supabase.env"))
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        raise SystemExit("SUPABASE_DB_URL is required")
    creds = (os.environ.get("DRIVE_CREDENTIALS")
             or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
    if not creds or not Path(creds).is_file():
        creds = _DEV_CREDS
    if not Path(creds).is_file():
        raise SystemExit("DRIVE_CREDENTIALS (service-account JSON) is required")
    return {
        "db_url": db_url,
        "drive_creds": creds,
        "scan_interval": int(os.environ.get("SCAN_INTERVAL_MINUTES", "30")) * 60,
        "poll_interval": int(os.environ.get("POLL_INTERVAL_MINUTES", "5")) * 60,
    }


def _scan_once(db, drive_client) -> None:
    def resolver(ref):
        return (drive_api.node_from_ref(ref, drive_client)
                if drive_api.is_drive_ref(ref) else ref)

    result = tracker.run_scan(db, drive_client.root(), folder_resolver=resolver)
    log.info("scan: %d imported, %d vessel changes, %d done",
             len(result.imported), len(result.vessel_changes),
             len(result.completed))
    for warning in result.warnings:
        log.warning("scan: %s", warning)


def _poll_once(db, bnct_client) -> None:
    monitor = BnctMonitor(db)
    board = VesselMonitor(db)
    containers = Containers(db)
    notifications = NotificationStore(db)

    vessels = bnct_client.fetch_vessels()
    for row in monitor.monitored():
        label = f"{row['exporter_code']}{row['sequence_number']}"
        monitor.process(row["id"], label, build_reading(row, vessels))
    for row in board.monitored():
        reading = bnct.read_for_shipment(
            vessels, row["vessel_name"] or "", row["voyage"] or "")
        board.process(row["id"], reading)
    _poll_containers(bnct_client, containers, notifications, vessels)
    log.info("poll: %d vessels on portal, %d shipment monitors, %d board voyages",
             len(vessels), len(monitor.monitored()), len(board.monitored()))


def _poll_containers(bnct_client, containers, notifications, vessels) -> None:
    """Poll containers only for active shipments whose vessel is alongside and not
    departing — 51-STACK RECEIVING only happens then. Notify on the transition."""
    rows, watchable = [], {}
    for row in containers.active_with_vessel():
        key = (row["vessel_name"] or "", row["voyage"] or "")
        if key not in watchable:
            reading = bnct.read_for_shipment(vessels, key[0], key[1])
            watchable[key] = reading.found and not reading.departing
        if watchable[key]:
            rows.append(row)
    if not rows:
        return
    numbers = sorted({r["container_no"] for r in rows})
    try:
        cards = bnct_client.fetch_containers_batch(numbers)
    except Exception:      # noqa: BLE001 - container polling is best-effort
        return
    stamp = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        card = bnct.match_container(
            cards.get(row["container_no"], []),
            row["vessel_name"] or "", row["voyage"] or "")
        if card is None:
            continue
        previous = containers.update_status(
            row["id"], site=card.site, status_code=card.status_code,
            status_text=card.status_text, type=card.type, checked_at=stamp)
        if previous != bnct.STACK_RECEIVING_CODE and card.at_stack_receiving:
            label = f"{row['exporter_code']}{row['sequence_number']}"
            notifications.add(
                "container", row["shipment_id"],
                f"{label}: {row['container_no']} STACK RECEIVING",
                f"Kontainer {row['container_no']} sedang diterima di stack BNCT "
                f"({card.site}). Kapal {row['vessel_name']} {row['voyage'] or ''}"
                .rstrip() + ".", created_at=stamp)


def _safe(fn, label: str) -> None:
    try:
        fn()
    except Exception as exc:      # noqa: BLE001 - a cycle must never kill the worker
        log.exception("%s failed: %s", label, exc)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = _config()
    db = PostgresDatabase(cfg["db_url"])
    db.initialize()
    drive_client = drive_api.DriveClient(cfg["drive_creds"])
    bnct_client = bnct.HttpBnctClient()

    if "--once" in sys.argv:
        _safe(lambda: _poll_once(db, bnct_client), "poll")
        _safe(lambda: _scan_once(db, drive_client), "scan")
        return

    log.info("worker started (scan every %ds, poll every %ds)",
             cfg["scan_interval"], cfg["poll_interval"])
    last_scan = last_poll = 0.0
    while True:
        now = time.monotonic()
        if now - last_poll >= cfg["poll_interval"]:
            _safe(lambda: _poll_once(db, bnct_client), "poll")
            last_poll = time.monotonic()
        if now - last_scan >= cfg["scan_interval"]:
            _safe(lambda: _scan_once(db, drive_client), "scan")
            last_scan = time.monotonic()
        time.sleep(20)


if __name__ == "__main__":
    main()
