"""Deterministic carrier and booking-number detection from DO text.

Both are read with regex rather than left to the LLM. The booking number in
particular is load-bearing — it goes into the folder name, the VGM sheet and
every email subject — and on an Evergreen DO it sits on the same line as a
second, similar-looking number:

    BOOKING NO. : 084600048570 APPLICATION NO.:26070303078721

The LLM returned the application number here, so the value is now taken from
the text directly and the LLM's answer is only a fallback.
"""

from __future__ import annotations

import re

# Carrier -> strings that identify it. Matched case-insensitively; the carrier
# whose marker appears earliest in the document wins, since the letterhead is
# at the top.
CARRIER_MARKERS: dict[str, tuple[str, ...]] = {
    "EVERGREEN": ("EVERGREEN LINE", "EVERGREEN MARINE", "EVERGREEN"),
    "OOCL": ("OOCL", "ORIENT OVERSEAS"),
    "PIL": ("PACIFIC INTERNATIONAL LINES", "PIL PTE"),
    "YANG MING": ("YANG MING", "YMING"),
    "MSC": ("MEDITERRANEAN SHIPPING", "MSC "),
    "ONE": ("OCEAN NETWORK EXPRESS",),
    "MAERSK": ("MAERSK",),
    "CMA CGM": ("CMA CGM",),
    "COSCO": ("COSCO",),
    "HAPAG-LLOYD": ("HAPAG-LLOYD", "HAPAG LLOYD"),
    "WAN HAI": ("WAN HAI", "WHL"),
    "VOLTA": ("VOLTA",),
    "FEEDERLINE": ("FEEDERLINE", "MAPFDLINE"),
}

# Tried in order. Each must capture the number itself, stopping before any
# following label on the same line.
#
# Horizontal whitespace only — `\s` would match a newline and let the pattern
# run onto the following line, which turned "BOOKING NO. :\nGREEN CELESTE..."
# into a booking number of "GREEN".
BOOKING_PATTERNS = (
    re.compile(r"BOOKING[ \t]*NO\.?[ \t]*:[ \t]*([A-Z0-9][A-Z0-9\-/]{4,})",
               re.IGNORECASE),
    re.compile(r"BOOKING[ \t]*NUMBER[ \t]*:[ \t]*([A-Z0-9][A-Z0-9\-/]{4,})",
               re.IGNORECASE),
    re.compile(r"BOOKING[ \t]*REF(?:ERENCE)?[ \t]*(?:NO\.?)?[ \t]*:[ \t]*"
               r"([A-Z0-9][A-Z0-9\-/]{4,})", re.IGNORECASE),
)

# Every real booking number seen carries at least four digits (084600040226,
# 2331048250, S420101257, T22854, MESG00021801). Words like GREEN or CELESTE
# carry none, so this rejects a vessel name that drifts into the capture.
MIN_DIGITS = 4

# A label whose value did not survive on the same line. Evergreen's two-column
# layout makes pdfplumber emit the value on the line *before* its label:
#
#     084600048570 APPLICATION NO.:26070303078721
#     BOOKING NO. :
#
# so a bare label means "look at the previous line". This is also why the model
# picked the application number: that one *is* adjacent to its own label.
BARE_LABEL_RE = re.compile(
    r"^\s*BOOKING\s*(?:NO\.?|NUMBER|REF(?:ERENCE)?(?:\s*NO\.?)?)\s*:?\s*$",
    re.IGNORECASE)

VALUE_TOKEN_RE = re.compile(r"^([A-Z0-9][A-Z0-9\-/]{4,})\b", re.IGNORECASE)

# Never accept these as a booking number, even if a pattern reaches them.
FORBIDDEN_LABELS = ("APPLICATION", "RATE", "CONTRACT", "AGREEMENT", "REFERENCE NUMBER")


# Only the top of the document counts as letterhead. Searching the whole text
# produced false positives: a Yang Ming DO names "OOCL LE HAVRE 208W" as the
# connecting vessel, which is not the carrier.
HEADER_LINES = 8
CARRIER_LABEL_RE = re.compile(r"^\s*CARRIER\s*:", re.IGNORECASE)
VESSEL_LINE_RE = re.compile(r"VESSEL|VOY|CONNECT", re.IGNORECASE)


def _match_in(fragment: str) -> str | None:
    haystack = fragment.upper()
    best: tuple[int, str] | None = None
    for name, markers in CARRIER_MARKERS.items():
        for marker in markers:
            index = haystack.find(marker.upper())
            if index == -1:
                continue
            if best is None or index < best[0]:
                best = (index, name)
            break
    return best[1] if best else None


def detect_carrier(text: str) -> str | None:
    """Identify the carrier from the document. None when it cannot be told.

    Returning None is preferable to guessing: the worker then picks the
    shipping company manually instead of being handed a wrong one.
    """
    if not text:
        return None
    lines = text.splitlines()

    # 1. An explicit "CARRIER :" line is authoritative (Evergreen).
    for line in lines:
        if CARRIER_LABEL_RE.match(line):
            found = _match_in(line)
            if found:
                return found

    # 2. Otherwise the letterhead, skipping vessel lines, which name other
    #    carriers' ships.
    for line in lines[:HEADER_LINES]:
        if VESSEL_LINE_RE.search(line):
            continue
        found = _match_in(line)
        if found:
            return found
    return None


def _clean(value: str) -> str | None:
    value = value.strip().rstrip(".,;")
    if not value:
        return None
    if any(label in value.upper() for label in FORBIDDEN_LABELS):
        return None
    if sum(character.isdigit() for character in value) < MIN_DIGITS:
        return None
    return value


def extract_booking_number(text: str) -> str | None:
    """Pull the booking number straight out of the DO text.

    Handles both layouts: the value on the same line as its label, and the
    value on the line before it (see BARE_LABEL_RE).
    """
    if not text:
        return None

    # 1. Value on the same line as the label.
    for pattern in BOOKING_PATTERNS:
        for match in pattern.finditer(text):
            value = _clean(match.group(1))
            if value:
                return value

    # 2. Bare label — the value sits on the preceding non-empty line, as its
    #    first token (the rest of that line belongs to the next column).
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not BARE_LABEL_RE.match(line):
            continue
        for previous in range(index - 1, -1, -1):
            candidate = lines[previous].strip()
            if not candidate:
                continue
            token = VALUE_TOKEN_RE.match(candidate)
            if token:
                value = _clean(token.group(1))
                if value:
                    return value
            break
    return None


def looks_like_booking_number(candidate: str, text: str) -> bool:
    """Is `candidate` actually labelled as the booking number in the document?

    Used to decide whether to trust the LLM when the regex finds nothing.
    """
    if not candidate or not text:
        return False
    return extract_booking_number(text) == candidate.strip()
