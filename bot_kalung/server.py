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

import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from .core.pg import PostgresDatabase
from .core.settings import Settings
from .services import bnct, drive_api, ntfy, tracker
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


def _drive_credentials():
    """Service-account credentials as a dict (from DRIVE_CREDENTIALS_JSON, for
    cloud hosts that inject secrets as env vars) or a file path (DRIVE_CREDENTIALS
    / GOOGLE_APPLICATION_CREDENTIALS, for a VM), falling back to the dev key."""
    raw = os.environ.get("DRIVE_CREDENTIALS_JSON")
    if raw:
        import json
        return json.loads(raw)
    path = (os.environ.get("DRIVE_CREDENTIALS")
            or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
    if not path or not Path(path).is_file():
        path = _DEV_CREDS
    if not Path(path).is_file():
        raise SystemExit(
            "Drive credentials required: set DRIVE_CREDENTIALS_JSON or "
            "DRIVE_CREDENTIALS")
    return path


def _config() -> dict:
    _load_env_file(Path("secrets/supabase.env"))
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        raise SystemExit("SUPABASE_DB_URL is required")
    return {
        "db_url": db_url,
        "drive_creds": _drive_credentials(),
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
    _store_scan_report(db, result)
    try:
        _resolve_photo_folders(db, drive_client)
    except Exception:      # noqa: BLE001 - photo linking must never fail a scan
        log.exception("photo resolution failed")


def _store_scan_report(db, result) -> None:
    """Persist the scan outcome to `settings.last_scan_report` (JSON) so the PWA's
    Pindai Folder screen can show it."""
    report = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "summary": result.summary,
        "imported": list(result.imported),
        "vessel_changes": list(result.vessel_changes),
        "completed": list(result.completed),
        "warnings": list(result.warnings),
    }
    try:
        Settings(db).set("last_scan_report", json.dumps(report))
    except Exception:      # noqa: BLE001 - reporting is best-effort
        log.exception("could not store scan report")


def _resolve_photo_folders(db, drive_client) -> None:
    """Best-effort: match each active shipment's containers to their photo subfolder
    under the shipment's 'Foto' dir and store the Drive ref, so the PWA can link to
    it. Only fills containers without a ref yet, so repeat scans are near-free."""
    rows = db.query(
        "SELECT c.id, c.container_no, s.folder_path FROM containers c "
        "JOIN shipments s ON s.id = c.shipment_id "
        "WHERE s.status='active' AND c.photo_folder_ref IS NULL "
        "AND s.folder_path IS NOT NULL")
    if not rows:
        return
    by_folder: dict[str, list] = {}
    for row in rows:
        by_folder.setdefault(row["folder_path"], []).append(row)

    def norm(value: str | None) -> str:
        return re.sub(r"[^A-Z0-9]", "", (value or "").upper())

    filled = 0
    for folder_ref, conts in by_folder.items():
        if not drive_api.is_drive_ref(folder_ref):
            continue
        try:
            node = drive_api.node_from_ref(folder_ref, drive_client)
            foto = next((ch for ch in node.iterdir()
                         if ch.is_dir() and ch.name.strip().lower() == "foto"), None)
            if foto is None:
                continue
            subdirs = [ch for ch in foto.iterdir() if ch.is_dir()]
        except Exception:      # noqa: BLE001 - one bad folder must not stop the rest
            continue
        for row in conts:
            number = norm(row["container_no"])
            match = next((d for d in subdirs if number and number in norm(d.name)), None)
            if match is not None:
                db.execute("UPDATE containers SET photo_folder_ref=? WHERE id=?",
                           (match.ref, row["id"]))
                filled += 1
    if filled:
        log.info("photos: linked %d container folder(s)", filled)


def _poll_once(db, bnct_client) -> None:
    monitor = BnctMonitor(db)
    board = VesselMonitor(db)
    containers = Containers(db)
    notifications = NotificationStore(db)

    # Notifications that already exist, so we push only the ones this cycle adds.
    seen = {row["id"] for row in db.query("SELECT id FROM notifications")}

    vessels = bnct_client.fetch_vessels()
    for row in monitor.monitored():
        label = f"{row['exporter_code']}{row['sequence_number']}"
        monitor.process(row["id"], label, build_reading(row, vessels))
    for row in board.monitored():
        reading = bnct.read_for_shipment(
            vessels, row["vessel_name"] or "", row["voyage"] or "")
        board.process(row["id"], reading)
    _poll_containers(bnct_client, containers, notifications, vessels)
    _push_new_notifications(db, seen)
    log.info("poll: %d vessels on portal, %d shipment monitors, %d board voyages",
             len(vessels), len(monitor.monitored()), len(board.monitored()))


def _push_new_notifications(db, seen_ids: set) -> None:
    """ntfy-push each notification created during this cycle (id not seen before).

    Gated by the shared `ntfy_enabled` setting so the PWA's toggle controls it.
    Fire-and-forget: ntfy.publish never raises, so a failed push cannot break a
    poll. The whole database's notifications are cheap to re-list at this scale."""
    settings = Settings(db)
    if not settings.get_bool("ntfy_enabled"):
        return
    pushed = 0
    for row in db.query(
            "SELECT id, kind, title, body FROM notifications ORDER BY created_at"):
        if row["id"] in seen_ids:
            continue
        if ntfy.publish(settings, row["title"], row["body"] or "", kind=row["kind"]):
            pushed += 1
    if pushed:
        log.info("ntfy: pushed %d new notification(s)", pushed)


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


def _interval(settings, key: str, default_secs: int) -> float:
    """Cadence in seconds from the settings table (stored in minutes), so the PWA
    can retune it, falling back to the env/default. A bad value never stalls the
    loop."""
    try:
        raw = settings.get(key)
        if raw:
            return max(60.0, int(raw) * 60)
    except Exception:      # noqa: BLE001
        pass
    return default_secs


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

    settings = Settings(db)
    log.info("worker started (default scan every %ds, poll every %ds; "
             "intervals overridable from the PWA)",
             cfg["scan_interval"], cfg["poll_interval"])
    last_scan = last_poll = 0.0
    try:
        last_scan_req = settings.get("scan_requested_at") or ""   # ignore a stale one at boot
    except Exception:      # noqa: BLE001
        last_scan_req = ""
    while True:
        now = time.monotonic()
        poll_interval = _interval(settings, "poll_interval_minutes", cfg["poll_interval"])
        scan_interval = _interval(settings, "scan_interval_minutes", cfg["scan_interval"])
        if now - last_poll >= poll_interval:
            _safe(lambda: _poll_once(db, bnct_client), "poll")
            last_poll = time.monotonic()
        try:
            scan_req = settings.get("scan_requested_at") or ""    # on-demand from the PWA
        except Exception:      # noqa: BLE001
            scan_req = last_scan_req
        if now - last_scan >= scan_interval or scan_req != last_scan_req:
            if scan_req != last_scan_req:
                log.info("scan: on-demand request (%s)", scan_req)
            _safe(lambda: _scan_once(db, drive_client), "scan")
            last_scan = time.monotonic()
            last_scan_req = scan_req
        time.sleep(20)


if __name__ == "__main__":
    main()
