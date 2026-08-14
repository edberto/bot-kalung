"""Search active shipments (folder-scan tracker).

For now two dimensions, per the user: container number and party size. Returns a
flat, de-duplicated list of matches with a short reason so the UI can show why
each shipment came up.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.db import Database


@dataclass
class SearchResult:
    shipment_id: str
    code: str             # "NIT" — for the exporter-coloured badge
    label: str            # "NIT16"
    subtitle: str         # "INTEGRA 184E · Chennai · 5 x 40'HC"
    match: str            # why it matched ("Kontainer CMAU8513405")


def _label(row) -> str:
    return f"{row['exporter_code']}{row['sequence_number']}"


def _party(row) -> str:
    return (f"{row['container_quantity'] or '?'} x "
            f"{row['container_size_short'] or '?'}")


def _subtitle(row) -> str:
    vessel = f"{row['vessel_name'] or ''} {row['voyage'] or ''}".strip() or "-"
    return f"{vessel} · {row['destination_port'] or '-'} · {_party(row)}"


def search_shipments(db: Database, query: str) -> list[SearchResult]:
    """Active shipments matching `query` by container number or party size."""
    q = (query or "").strip()
    if not q:
        return []
    results: dict[str, SearchResult] = {}

    # -- by container number ------------------------------------------------
    for row in db.query(
            "SELECT s.*, c.container_no FROM shipments s "
            "JOIN containers c ON c.shipment_id = s.id "
            "WHERE s.status='active' "
            "AND UPPER(c.container_no) LIKE '%' || UPPER(?) || '%'", (q,)):
        results.setdefault(row["id"], SearchResult(
            row["id"], row["exporter_code"], _label(row), _subtitle(row),
            f"Kontainer {row['container_no']}"))

    # -- by party size (the count, or the size text) ------------------------
    for row in db.query("SELECT * FROM shipments WHERE status='active'"):
        if row["id"] in results:
            continue
        party = _party(row).upper()
        if (q.isdigit() and row["container_quantity"] == int(q)) \
                or q.upper() in party:
            results[row["id"]] = SearchResult(
                row["id"], row["exporter_code"], _label(row), _subtitle(row),
                f"Party {_party(row)}")

    return sorted(results.values(), key=lambda r: r.label)
