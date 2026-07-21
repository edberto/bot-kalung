"""SI auto-print (PRD Section 5).

Uses Excel's own page setup — the app never changes print settings. A print
failure must never break the creation flow, so nothing here raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import excel


@dataclass
class PrintResult:
    printed: bool
    message: str


def print_si(workbook_path) -> PrintResult:
    """Print the SI sheet. Never raises."""
    path = Path(workbook_path)
    if not path.is_file():
        return PrintResult(False, f"File Excel tidak ditemukan: {path}")

    try:
        with excel.open_book(path) as book:
            sheet = excel.find_sheet(book, "SI")
            if sheet is None:
                return PrintResult(False, "Sheet SI tidak ditemukan.")
            try:
                sheet.api.PrintOut()
            except Exception as exc:  # noqa: BLE001 - PRD 5: warn, never block
                return PrintResult(
                    False,
                    "Gagal mencetak SI secara otomatis. Cetak manual dari Excel. "
                    f"({exc})")
            return PrintResult(True, "SI dicetak.")
    except Exception as exc:  # noqa: BLE001 - a print must never block creation
        return PrintResult(
            False,
            "Gagal membuka file Excel untuk mencetak SI. Cetak manual. "
            f"({exc})")
