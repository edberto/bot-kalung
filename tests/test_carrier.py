"""Carrier detection and booking-number extraction.

Regression cover for the 2026-07-20 report: on the Evergreen DO the LLM
returned the APPLICATION NO. (26070303078721) instead of the BOOKING NO.
(084600048570). Both sit on the same line.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.services import carrier
from bot_kalung.services.do_parser import extract_fields, extract_text

EVERGREEN_DO = Path(r"G:\My Drive\AMJ\2026"
                    r"\23.2x40-karachi-Salim-KLN-EVE-084600048570-Concert 0800-088N-03 aug"
                    r"\084600048570 ETD BLW 03 AUG.PDF")
OOCL_DO = Path(r"G:\My Drive\AMJ\2026"
               r"\18.3x40-KARACHI-Taheer-SUNLIE-OOCL-2331048250-INTEGRA181"
               r"\2331048250 (1).pdf")

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


# ---- the exact failing line from the Evergreen DO -------------------------
EVERGREEN_LINE = ("BOOKING NO. : 084600048570 APPLICATION NO.:26070303078721\n"
                  "VESSEL/VOYAGE : EVER CONCERT 0800-088N\n"
                  "CARRIER :EVERGREEN LINE\n")

check("booking number taken from BOOKING NO., not APPLICATION NO.",
      carrier.extract_booking_number(EVERGREEN_LINE) == "084600048570")
check("application number is never returned",
      carrier.extract_booking_number(EVERGREEN_LINE) != "26070303078721")
check("Evergreen detected", carrier.detect_carrier(EVERGREEN_LINE) == "EVERGREEN")

OOCL_LINE = ("2331048250\nBy OOCL App\nBooking Acknowledgement\n"
             "BOOKING NUMBER: 2331048250\nBOOKING STATUS: Confirmed\n"
             "RATE AGREEMENT NUMBER: 00128472\n")
check("OOCL booking number extracted",
      carrier.extract_booking_number(OOCL_LINE) == "2331048250")
check("rate agreement number is not mistaken for the booking",
      carrier.extract_booking_number(OOCL_LINE) != "00128472")
check("OOCL detected", carrier.detect_carrier(OOCL_LINE) == "OOCL")

check("unknown carrier returns None",
      carrier.detect_carrier("SOME OTHER SHIPPING FORM") is None)

# A connecting vessel named after another carrier must not be mistaken for the
# carrier itself (the Yang Ming DO names "OOCL LE HAVRE 208W").
VESSEL_TRAP = ("BOOKING CONFIRMATION\nDate: 29/05/2026\nTo: PT. GAP LOGISTICS\n"
               "Booking Ref: S420101257 SI No : ETD : 16/06/2026\n"
               "Vessel Name:MAO GANG GUANG ZHOU 015N ( BSS2624N ) / "
               "OOCL LE HAVRE 208W (\n")
check("a connecting vessel name is not treated as the carrier",
      carrier.detect_carrier(VESSEL_TRAP) is None)
check("booking still extracted from that form",
      carrier.extract_booking_number(VESSEL_TRAP) == "S420101257")
check("CARRIER: line wins over anything later in the document",
      carrier.detect_carrier(
          "BOOKING CONFIRMATION\nCARRIER :EVERGREEN LINE\n"
          "EST. CONNECT VSL/VOY:OOCL LE HAVRE 209W\n") == "EVERGREEN")
check("empty text returns None", carrier.detect_carrier("") is None)
check("no booking number returns None",
      carrier.extract_booking_number("nothing here") is None)

check("candidate validation accepts the real booking",
      carrier.looks_like_booking_number("084600048570", EVERGREEN_LINE))
check("candidate validation rejects the application number",
      not carrier.looks_like_booking_number("26070303078721", EVERGREEN_LINE))

# The real Evergreen PDF puts the value on the line BEFORE its label, because
# of the two-column layout.
INVERTED = ("******** BOOKING CONFIRMATION / REVISE : 000 ********\n"
            "084600048570 APPLICATION NO.:26070303078721\n"
            "BOOKING NO. :\n"
            "EVER CONCERT 0800-088N\n"
            "VESSEL/VOYAGE :\n")
check("value on the line before a bare label is found",
      carrier.extract_booking_number(INVERTED) == "084600048570")
check("inverted layout still rejects the application number",
      carrier.extract_booking_number(INVERTED) != "26070303078721")


# ---- end to end through the parser, with a deliberately wrong LLM ---------
class WrongClient:
    """Returns the application number, exactly as observed."""

    def extract_do_fields(self, raw_text):
        return {
            "booking_number": "26070303078721",
            "vessel_name": "EVER CONCERT", "voyage": "0800-088N",
            "etd_belawan": "2026-08-03", "destination_port": "Karachi",
            "destination_country": "Pakistan", "container_quantity": 2,
            "container_size_raw": "40' HI-CUBE",
            "empty_pickup_location": "PT SINAR JATIMITRA",
        }


result = extract_fields(WrongClient(), EVERGREEN_LINE)
check("parser overrides a wrong LLM booking number",
      result.booking_number == "084600048570")
check("parser records why it overrode",
      any("084600048570" in note for note in result.notes))
check("parser fills in the carrier", result.carrier == "EVERGREEN")
check("other fields still come from the model",
      result.vessel_name == "EVER CONCERT"
      and result.etd_belawan == date(2026, 8, 3))


# ---- against the real PDFs -------------------------------------------------
for label, path, expected_booking, expected_carrier in (
        ("Evergreen", EVERGREEN_DO, "084600048570", "EVERGREEN"),
        ("OOCL", OOCL_DO, "2331048250", "OOCL")):
    if not path.is_file():
        print(f"SKIP  {label} DO not reachable")
        continue
    text = extract_text(path)
    check(f"{label} DO: booking number read correctly",
          carrier.extract_booking_number(text) == expected_booking)
    check(f"{label} DO: carrier detected as {expected_carrier}",
          carrier.detect_carrier(text) == expected_carrier)

# The regex must not run past the end of the label's own line. "BOOKING NO. :"
# followed by "GREEN CELESTE 0795-126N" previously yielded "GREEN", because \s
# matches newlines and GREEN is long enough to satisfy the capture.
GREEN = ("084600040226 APPLICATION NO.:26060310117492\n"
         "BOOKING NO. :\n"
         "GREEN CELESTE 0795-126N\n"
         "VESSEL/VOYAGE :\n")
check("vessel name on the next line is not captured",
      carrier.extract_booking_number(GREEN) == "084600040226")
check("a wordy token is rejected outright",
      carrier.extract_booking_number("BOOKING NO. :\nGREEN CELESTE\n") is None)
check("a token without enough digits is rejected",
      carrier.extract_booking_number("BOOKING NUMBER: ABCDEFGH") is None)
check("an alphanumeric booking is still accepted",
      carrier.extract_booking_number("BOOKING NO. : S420101257") == "S420101257")
check("a short-but-numeric booking is accepted",
      carrier.extract_booking_number("BOOKING NO. : T22854") == "T22854")

# Every DO available locally, so a layout quirk in one form cannot pass unseen.
SANDBOX_DOS = Path(r"d:\side-projects\bot-kalung\_sandbox\DO")
if SANDBOX_DOS.is_dir():
    print("\n-- every sample DO --")
    for pdf in sorted(SANDBOX_DOS.iterdir()):
        if pdf.suffix.lower() != ".pdf":
            continue
        text = extract_text(pdf)
        found = carrier.extract_booking_number(text)
        line = carrier.detect_carrier(text)
        # The filename leads with the booking number for these samples.
        stem = pdf.stem.replace("BC ", "").split(" ")[0].split("(")[0].strip()
        ok = bool(found) and (found == stem or stem.startswith(found))
        print(f"{'PASS' if ok else 'FAIL'}  {pdf.name[:42]:42} -> "
              f"{found} / {line}")
        if not ok:
            failures.append(f"booking from {pdf.name}")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Carrier/booking OK - all checks passed.")
