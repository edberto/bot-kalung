"""Shared formatting of a stored BNCT reading for the two monitoring views.

`record` is either a `bnct_checks` row or a decoded `monitored_vessels.last_reading`
snapshot — both index by the same column names, so one formatter serves both the
per-shipment panel and the standalone Monitor Kapal card. Bahasa Indonesia,
no Qt, so it stays trivially testable.
"""

from __future__ import annotations


def _get(record, key):
    try:
        return record[key]
    except (KeyError, IndexError):
        return None      # an older row/snapshot predates this column


def _fmt(value) -> str:
    return "-" if value in (None, "") else str(value)


def _pct(actual, plan) -> str:
    if plan in (None, 0) or actual is None:
        return ""
    return f"  ({round(max(0, min(actual, plan)) / plan * 100)}% selesai)"


def _schedule_line(record) -> list[str]:
    """ETD / Clossing / Clossing Reefer / Open Billing / Open Stack, blanks
    omitted, one field per line. Shown for a scheduled vessel and carried
    forward once it berths."""
    pairs = [
        ("ETD", _get(record, "etd")),
        ("Clossing", _get(record, "clossing")),
        ("Clossing Reefer", _get(record, "clossing_reefer")),
        ("Open Billing", _get(record, "open_billing")),
        ("Open Stack", _get(record, "open_stacking")),
    ]
    return [f"{label}: {value}" for label, value in pairs if value]


def describe_record(record) -> tuple[str, str, str]:
    """(colour, status text, multi-line detail) for a stored reading."""
    if not _get(record, "found"):
        return ("#6b7280", "Kapal belum terjadwal di BNCT.", "")

    if _get(record, "phase") == "schedule":
        return ("#2563eb", "Terjadwal di BNCT",
                "\n".join(_schedule_line(record)))

    # alongside — operations, then the carried-forward schedule fields
    load_pct = _pct(_get(record, "loading_actual"), _get(record, "loading_plan"))
    ops = [
        f"Loading: sisa {_fmt(_get(record, 'loading_remain'))} dari "
        f"{_fmt(_get(record, 'loading_plan'))}{load_pct}",
        f"Discharge: sisa {_fmt(_get(record, 'discharge_remain'))} dari "
        f"{_fmt(_get(record, 'discharge_plan'))}",
        f"Restow: sisa {_fmt(_get(record, 'restow_remain'))} dari "
        f"{_fmt(_get(record, 'restow_plan'))}",
        f"ATB {_fmt(_get(record, 'atb'))}",
    ]
    detail = "\n".join(ops + _schedule_line(record))
    if _get(record, "departing"):
        return ("#b91c1c", "Kapal akan berangkat.", detail)
    return ("#059669", "Kapal sudah sandar", detail)
