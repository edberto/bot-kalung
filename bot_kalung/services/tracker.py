"""Apply a folder scan to the database (folder-scan tracker).

`scanner.scan` decides *what* to do; this module *does* it: it reads each new
shipment's fields from its workbook, inserts the `shipments` row, and records the
`scanned_shipments` registry so the next scan skips it. Kept separate from the
pure scanner so the discovery/gate rules stay testable without a database, and
separate from the UI so the whole import is testable without Qt (the workbook
read and the BL page reader are both injectable).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime

from ..core.constants import DEFAULT_QUARANTINE_COUNTRIES
from ..core.db import Database, new_id
from . import excel, naming, scanner, workbook
from .action_items import ActionItems
from .containers import Containers
from .shipments import Shipments
from .vessel_monitor import MonitoredVessels


def _ensure_vessel_monitored(vessels: MonitoredVessels, name: str | None,
                             voyage: str | None) -> None:
    """Auto-monitor a shipment's vessel+voyage on the Monitor Kapal board, unless
    an existing monitored voyage already covers it (matched fuzzily like BNCT).
    A shipment with no vessel/voyage yet is skipped.
    """
    if not name or not voyage:
        return
    from .bnct import _name_matches, _voyage_matches

    for row in vessels.all():
        if (_name_matches(name, row["vessel_name"] or "")
                and _voyage_matches(voyage, row["voyage"] or "")):
            return
    vessels.add_vessel(name, voyage)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _folder_ref(folder) -> str:
    """The re-openable reference stored as shipments.folder_path: a filesystem
    path for a local scan, or 'drive:{id}' for a Drive-API scan (a Drive node
    exposes it as `.ref`)."""
    ref = getattr(folder, "ref", None)
    return ref if ref is not None else str(folder)


class ScannedRegistry:
    """The `scanned_shipments` table — what the tracker has already handled."""

    def __init__(self, db: Database):
        self.db = db

    def registered_keys(self) -> set[tuple[str, int]]:
        rows = self.db.query(
            "SELECT exporter_code, sequence_number FROM scanned_shipments")
        return {(r["exporter_code"], r["sequence_number"]) for r in rows}

    def record(self, code: str, sequence: int, folder_path: str | None,
               *, done: bool, shipment_id: str | None = None) -> None:
        # INSERT OR IGNORE on the (code, seq) unique key: a scan should never
        # collide (registered keys are filtered out first), but this keeps a
        # racing second scan from raising instead of quietly no-op'ing.
        self.db.execute(
            "INSERT OR IGNORE INTO scanned_shipments "
            "(id, exporter_code, sequence_number, folder_path, done, "
            " shipment_id, imported_at) VALUES (?,?,?,?,?,?,?)",
            (new_id(), code, sequence, folder_path,
             1 if done else 0, shipment_id, _now()))

    def rows(self) -> list:
        return self.db.query(
            "SELECT * FROM scanned_shipments "
            "ORDER BY exporter_code, sequence_number")


@dataclass
class ScanApplyResult:
    imported: list[str] = field(default_factory=list)    # {code}{seq} labels
    completed: list[str] = field(default_factory=list)   # recorded as done
    vessel_changes: list[str] = field(default_factory=list)  # "NIT16: A 1N → B 2N"
    report: list[str] = field(default_factory=list)      # discovery notes
    warnings: list[str] = field(default_factory=list)    # per-shipment read issues

    @property
    def summary(self) -> str:
        parts = [f"{len(self.imported)} pengiriman baru"]
        if self.completed:
            parts.append(f"{len(self.completed)} sudah selesai")
        if self.vessel_changes:
            parts.append(f"{len(self.vessel_changes)} perubahan kapal")
        return ", ".join(parts)


def _detect_vessel_voyage_changes(shipments: Shipments, vessels: MonitoredVessels,
                                  containers: Containers, reread_fields,
                                  skip_ids: set[str], result: ScanApplyResult,
                                  by_key: dict | None = None,
                                  mtime_cache: dict | None = None,
                                  folder_resolver=None) -> None:
    """Re-read active shipments' workbooks to (a) re-point any whose Drive folder
    was renumbered after import — `{code}{seq}` follows the folder that now carries
    that number, not the frozen import-time one — (b) update a vessel/voyage that
    moved, and (c) reconcile containers to the VGM (its source of truth), so a typo
    fix, a partial fill being completed, or numbers entered after import propagate.

    Container sync is safe: it no-ops on an empty/failed VGM read (never wipes),
    keeps unchanged numbers so their live BNCT status survives, and only adds/drops
    the difference. Changes land in `result.vessel_changes` / `result.report`. An
    unreadable workbook (e.g. an old .xls the headless reader can't open) reads as
    no vessel and is left untouched, never overwritten with a blank.

    `by_key` maps `(code, seq)` -> the folder that currently carries that number
    (from the scan's discovery). `folder_resolver` maps a stored folder_path to a
    folder handle for shipments not in `by_key`. Only a filesystem folder is
    mtime-gated (`mtime_cache`) — a Drive folder re-reads each scan.
    """
    by_key = by_key or {}
    for row in shipments.active():
        if row["id"] in skip_ids:
            continue
        # Prefer the folder that CURRENTLY carries this (code, seq): if it was
        # renumbered on Drive after import, re-point the shipment so its number
        # follows the folder. Otherwise fall back to the stored folder_path.
        current = by_key.get((row["exporter_code"], row["sequence_number"]))
        if current is not None:
            folder = current.folder
            current_ref = _folder_ref(folder)
            if current_ref != (row["folder_path"] or ""):
                shipments.set_folder_path(row["id"], current_ref)
                result.report.append(
                    f"{row['exporter_code']}{row['sequence_number']}: "
                    "folder dipetakan ulang (nomor folder berubah)")
        elif row["folder_path"]:
            folder = (folder_resolver(row["folder_path"]) if folder_resolver
                      else row["folder_path"])
        else:
            continue
        if mtime_cache is not None and not hasattr(folder, "ref"):  # local path, not a Drive node
            wb = excel.find_main_workbook(folder)
            if wb is not None:
                try:
                    mtime = os.path.getmtime(wb)
                except OSError:
                    mtime = None
                if mtime is not None:
                    if mtime_cache.get(row["id"]) == mtime:
                        continue          # unchanged since last scan
                    mtime_cache[row["id"]] = mtime
        try:
            fields = reread_fields(folder)
        except Exception:      # noqa: BLE001 - one bad workbook must not stop the scan
            continue

        # Reconcile containers to the VGM on every scan — it is the source of
        # truth, so a typo fix, a partial fill being completed, or numbers entered
        # after import all propagate. sync() no-ops on an empty read (never wipes),
        # keeps unchanged numbers (their BNCT status survives), drops removed ones.
        if fields.containers and containers.sync(
                row["id"], fields.containers, size=fields.container_size_short):
            if fields.container_quantity:
                shipments.set_party(row["id"], fields.container_quantity,
                                    fields.container_size_short)
            result.report.append(
                f"{row['exporter_code']}{row['sequence_number']}: "
                f"kontainer disinkronkan dari VGM ({len(fields.containers)})")

        new_vessel, new_voyage = fields.vessel_name, fields.voyage
        if not new_vessel or not new_voyage:
            continue           # unreadable / incomplete — don't overwrite

        old_vessel = (row["vessel_name"] or "").strip().upper()
        old_voyage = (row["voyage"] or "").strip().upper()
        if (new_vessel.strip().upper() == old_vessel
                and new_voyage.strip().upper() == old_voyage):
            continue           # no change

        shipments.set_vessel_voyage(row["id"], new_vessel, new_voyage)
        _ensure_vessel_monitored(vessels, new_vessel, new_voyage)
        label = f"{row['exporter_code']}{row['sequence_number']}"
        was = f"{row['vessel_name'] or '?'} {row['voyage'] or '?'}".strip()
        result.vessel_changes.append(f"{label}: {was} → {new_vessel} {new_voyage}")


def run_scan(db: Database, drive_root, *, year: int | None = None, settings=None,
             read_fields=workbook.read_shipment_fields,
             reread_fields=workbook.read_shipment_fields,
             mtime_cache: dict | None = None,
             folder_resolver=None) -> ScanApplyResult:
    """Scan the Drive and import every newly-eligible shipment.

    Both `read_fields` (import) and `reread_fields` (the vessel/voyage re-read)
    default to the headless workbook reader (openpyxl/xlrd), so a scan needs no
    Excel/COM. Both are injectable so the whole import can be driven in tests, and
    `excel.read_shipment_fields` (Excel/COM) can be passed in where Excel is
    preferred. Never raises for a single bad shipment — its problem lands in
    `warnings` and the scan continues.
    """
    year = year or date.today().year
    registry = ScannedRegistry(db)
    shipments = Shipments(db)
    action_items = ActionItems(db)
    containers = Containers(db)
    vessels = MonitoredVessels(db)
    quarantine = (settings.get("quarantine_countries") if settings else None) \
        or DEFAULT_QUARANTINE_COUNTRIES

    plan = scanner.scan(drive_root, year, registry.registered_keys(), settings)
    result = ScanApplyResult(report=plan.report)

    # Every shipment already in the DB, keyed by (code, seq). If the registry
    # drifted (a folder rename, a DB reset), a folder we re-discover may already
    # have a shipment — re-register it so the next scan skips it, but NEVER create
    # a duplicate and never modify the existing shipment row.
    known_keys = shipments.all_keys()
    imported_ids: set[str] = set()   # skip re-reading what we just imported

    for candidate in plan.to_import:
        if (candidate.code, candidate.sequence) in known_keys:
            registry.record(candidate.code, candidate.sequence,
                            _folder_ref(candidate.folder), done=False, shipment_id=None)
            result.report.append(f"{candidate.label}: sudah ada, dilewati")
            continue

        try:
            fields = read_fields(candidate.folder)
        except Exception as exc:      # noqa: BLE001 - one bad workbook, keep going
            result.warnings.append(f"{candidate.label}: gagal membaca Excel ({exc})")
            fields = excel.ShipmentFields()
        result.warnings += [f"{candidate.label}: {w}" for w in fields.warnings]

        shipment_id = shipments.create({
            "exporter_code": candidate.code,
            "sequence_number": candidate.sequence,
            "folder_path": _folder_ref(candidate.folder),
            "destination_port": fields.destination_port,
            "destination_country": fields.destination_country,
            "etd_belawan": fields.etd.isoformat() if fields.etd else None,
            "vessel_name": fields.vessel_name,
            "voyage": fields.voyage,
            "etd_source": "si" if fields.etd else None,
            "booking_number": fields.booking_number,
            "container_quantity": fields.container_quantity,
            "container_size_short": fields.container_size_short,
            "quarantine_required": naming.is_quarantine_required(
                fields.destination_country, quarantine),
        }, seed_steps=False)
        action_items.seed(shipment_id, candidate.code, fields.destination_country)
        containers.populate(shipment_id, fields.containers,
                            size=fields.container_size_short)
        _ensure_vessel_monitored(vessels, fields.vessel_name, fields.voyage)
        registry.record(candidate.code, candidate.sequence, _folder_ref(candidate.folder),
                        done=False, shipment_id=shipment_id)
        imported_ids.add(shipment_id)
        result.imported.append(candidate.label)

    for candidate in plan.done:
        registry.record(candidate.code, candidate.sequence, _folder_ref(candidate.folder),
                        done=True)
        result.completed.append(candidate.label)

    # After importing, re-read the active shipments to catch a vessel/voyage that
    # moved after import (the folder scan otherwise never revisits them).
    _detect_vessel_voyage_changes(shipments, vessels, containers, reread_fields,
                                  imported_ids, result, plan.by_key, mtime_cache,
                                  folder_resolver)
    return result
