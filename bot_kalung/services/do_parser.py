"""DO PDF reading and field extraction (PRD Section 8).

pdfplumber pulls the raw text; the LLM turns it into fields; everything after
that is deterministic post-processing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import carrier as carrier_service
from . import naming
from .llm import LLMClient, LLMError

REQUIRED_KEYS = [
    "booking_number", "vessel_name", "voyage", "etd_belawan",
    "destination_port", "destination_country", "container_quantity",
    "container_size_raw", "empty_pickup_location",
]


@dataclass
class ExtractedDO:
    booking_number: str = ""
    vessel_name: str = ""
    voyage: str = ""
    etd_belawan: date | None = None
    destination_port: str = ""
    destination_country: str = ""
    container_quantity: int = 1
    container_size_short: str = ""
    empty_pickup_location: str = ""
    carrier: str = ""
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """Every field the wizard requires before it will advance (PRD 5)."""
        return all([
            self.booking_number.strip(),
            self.vessel_name.strip(),
            self.voyage.strip(),
            self.etd_belawan is not None,
            self.destination_port.strip(),
            self.destination_country.strip(),
            self.container_quantity >= 1,
            self.container_size_short.strip(),
        ])


def extract_text(pdf_path) -> str:
    """PRD 8.1 step 1."""
    import pdfplumber

    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def extract_fields(client: LLMClient, raw_text: str) -> ExtractedDO:
    """PRD 8.1 steps 2-4 plus the 8.3 post-processing.

    Raises LLMError when the call fails outright; a call that succeeds but
    returns partial data yields an ExtractedDO with `missing` populated, so the
    wizard can show the "fill them in manually" banner (PRD Section 14).
    """
    data = client.extract_do_fields(raw_text)
    if not isinstance(data, dict):
        raise LLMError("Jawaban model bukan objek JSON.")

    result = ExtractedDO()
    missing: list[str] = []

    def text_of(key: str) -> str:
        value = data.get(key)
        return "" if value is None else str(value).strip()

    # Carrier is read from the letterhead, never from the model.
    result.carrier = carrier_service.detect_carrier(raw_text) or ""

    # Booking number: trust the document over the model. On an Evergreen DO the
    # application number sits on the same line and has been returned in its
    # place.
    llm_booking = text_of("booking_number")
    from_text = carrier_service.extract_booking_number(raw_text)
    if from_text:
        result.booking_number = from_text
        if llm_booking and llm_booking != from_text:
            result.notes.append(
                f"Nomor booking dari dokumen ({from_text}) dipakai, "
                f"bukan hasil model ({llm_booking}).")
    elif llm_booking and carrier_service.looks_like_booking_number(
            llm_booking, raw_text):
        result.booking_number = llm_booking
    else:
        result.booking_number = llm_booking

    result.vessel_name = text_of("vessel_name")
    result.voyage = text_of("voyage")
    result.destination_port = text_of("destination_port")
    result.destination_country = text_of("destination_country")
    result.empty_pickup_location = text_of("empty_pickup_location")

    result.etd_belawan = naming.parse_iso_date(data.get("etd_belawan"))

    quantity = data.get("container_quantity")
    try:
        result.container_quantity = max(1, int(quantity))
    except (TypeError, ValueError):
        result.container_quantity = 1
        if quantity is not None:
            missing.append("container_quantity")

    result.container_size_short = naming.container_size_short(
        text_of("container_size_raw"))

    for key in REQUIRED_KEYS:
        if key == "empty_pickup_location":
            continue  # optional (PRD Section 5)
        if key == "etd_belawan":
            if result.etd_belawan is None:
                missing.append(key)
            continue
        if key == "container_quantity":
            continue  # handled above
        if key == "container_size_raw":
            if not result.container_size_short:
                missing.append(key)
            continue
        if not text_of(key):
            missing.append(key)

    result.missing = sorted(set(missing))
    return result
