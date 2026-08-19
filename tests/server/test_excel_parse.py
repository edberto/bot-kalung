"""The pure workbook-field parsers (no Excel/COM needed).

These back both the Excel-COM reader and the headless openpyxl reader, so a fast
unit test guards them without a live workbook.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.services.excel import (
    _clean_booking,
    _parse_long_date,
    _parse_party,
    _split_destination,
    _split_vessel_voyage,
)

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


# ---- _parse_long_date: full and abbreviated months ------------------------
check("full month parses", _parse_long_date("27 JANUARY 2026") == date(2026, 1, 27))
check("abbreviated month parses", _parse_long_date("15 AUG 2026") == date(2026, 8, 15))
check("mixed-case abbrev parses", _parse_long_date("1 Sept 2026") == date(2026, 9, 1))
check("DEC abbrev parses", _parse_long_date("3 DEC 2026") == date(2026, 12, 3))
check("a real date passes through", _parse_long_date(date(2026, 8, 3)) == date(2026, 8, 3))
check("junk month is rejected", _parse_long_date("15 FOO 2026") is None)
check("non-date text is rejected", _parse_long_date("not a date") is None)
check("None stays None", _parse_long_date(None) is None)

# ---- _parse_party ---------------------------------------------------------
check("party splits count and size", _parse_party("5 X 40'HC") == (5, "40'HC"))
check("party is case-insensitive on X", _parse_party("2 x 20'") == (2, "20'"))
check("bad party yields nothing", _parse_party("no party here") == (None, None))

# ---- _split_vessel_voyage -------------------------------------------------
check("hyphen vessel/voyage splits",
      _split_vessel_voyage("INTEGRA-162E") == ("INTEGRA", "162E"))
check("space vessel/voyage splits",
      _split_vessel_voyage("MAO GANG GUANG ZHOU 021N")
      == ("MAO GANG GUANG ZHOU", "021N"))
check("a trailing pure number is part of the name, not a voyage",
      _split_vessel_voyage("WAN HAI 101") == ("WAN HAI 101", None))

# ---- _split_destination + _clean_booking ----------------------------------
check("destination splits port and country",
      _split_destination("KARACHI, PAKISTAN") == ("Karachi", "Pakistan"))
check("booking drops a float's trailing .0", _clean_booking(2318229000.0) == "2318229000")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Excel parse OK - all checks passed.")
