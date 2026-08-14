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
from . import excel, naming, scanner
from .shipments import Shipments


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
    report: list[str] = field(default_factory=list)      # discovery notes
    warnings: list[str] = field(default_factory=list)    # per-shipment read issues

    @property
    def summary(self) -> str:
        parts = [f"{len(self.imported)} pengiriman baru"]
        if self.completed:
            parts.append(f"{len(self.completed)} sudah selesai")
        return ", ".join(parts)


def run_scan(db: Database, drive_root, *, year: int | None = None, settings=None,
             read_fields=excel.read_shipment_fields,
             page1_text=scanner._default_page1_text) -> ScanApplyResult:
    """Scan the Drive and import every newly-eligible shipment.

    `read_fields` and `page1_text` are injectable so the whole import can be
    driven in tests without Excel or real PDFs. Never raises for a single bad
    shipment — its problem lands in `warnings` and the scan continues.
    """
    year = year or date.today().year
    registry = ScannedRegistry(db)
    shipments = Shipments(db)
    quarantine = (settings.get("quarantine_countries") if settings else None) \
        or DEFAULT_QUARANTINE_COUNTRIES

    plan = scanner.scan(drive_root, year, registry.registered_keys(), settings,
                        page1_text=page1_text)
    result = ScanApplyResult(report=plan.report)

    for candidate in plan.to_import:
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
            "booking_number": fields.booking_number,
            "container_quantity": fields.container_quantity,
            "container_size_short": fields.container_size_short,
            "quarantine_required": naming.is_quarantine_required(
                fields.destination_country, quarantine),
        }, seed_steps=False)
        registry.record(candidate.code, candidate.sequence, str(candidate.folder),
                        done=False, shipment_id=shipment_id)
        result.imported.append(candidate.label)

    for candidate in plan.done:
        registry.record(candidate.code, candidate.sequence, str(candidate.folder),
                        done=True)
        result.completed.append(candidate.label)

    return result
