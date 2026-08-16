"""Apply a folder scan to the database (folder-scan tracker).

`scanner.scan` decides *what* to do; this module *does* it: it reads each new
shipment's fields from its workbook, inserts the `shipments` row, and records the
`scanned_shipments` registry so the next scan skips it. Kept separate from the
pure scanner so the discovery/gate rules stay testable without a database, and
separate from the UI so the whole import is testable without Qt (the workbook
read and the BL page reader are both injectable).
"""

from __future__ import annotations

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
                                  reread_fields, skip_ids: set[str],
                                  result: ScanApplyResult) -> None:
    """Re-read active shipments' workbooks and update any whose vessel/voyage
    changed since import — a booking can be moved to a different vessel/voyage.

    Only vessel + voyage: containers are deliberately NOT re-read, so a manual
    container-number correction is never clobbered. Silent — changes land in
    `result.vessel_changes` (and the Monitor Kapal link is re-pointed). An
    unreadable workbook (e.g. old .xls the headless reader can't open) reads as
    no vessel and is left untouched, never overwritten with a blank.
    """
    for row in shipments.active():
        if row["id"] in skip_ids or not row["folder_path"]:
            continue
        try:
            fields = reread_fields(row["folder_path"])
        except Exception:      # noqa: BLE001 - one bad workbook must not stop the scan
            continue
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
             read_fields=excel.read_shipment_fields,
             reread_fields=workbook.read_shipment_fields) -> ScanApplyResult:
    """Scan the Drive and import every newly-eligible shipment.

    `read_fields` reads a new shipment's workbook on import (Excel/COM). Active
    shipments are then re-read with `reread_fields` (the headless openpyxl reader)
    to catch a vessel/voyage change. Both are injectable so the whole import can
    be driven in tests without Excel. Never raises for a single bad shipment — its
    problem lands in `warnings` and the scan continues.
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
                            str(candidate.folder), done=False, shipment_id=None)
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
            "folder_path": str(candidate.folder),
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
        registry.record(candidate.code, candidate.sequence, str(candidate.folder),
                        done=False, shipment_id=shipment_id)
        imported_ids.add(shipment_id)
        result.imported.append(candidate.label)

    for candidate in plan.done:
        registry.record(candidate.code, candidate.sequence, str(candidate.folder),
                        done=True)
        result.completed.append(candidate.label)

    # After importing, re-read the active shipments to catch a vessel/voyage that
    # moved after import (the folder scan otherwise never revisits them).
    _detect_vessel_voyage_changes(shipments, vessels, reread_fields,
                                  imported_ids, result)
    return result
