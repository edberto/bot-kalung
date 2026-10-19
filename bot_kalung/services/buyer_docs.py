"""AMI / PMA: the buyer's own documents are the source, not our workbook.

Their "Dok AMI" / "Dok PMA" folder holds the buyer's SI, VGM, CI (invoice) and
PL as text PDFs — sometimes in several versions ("Rev ke 3-CI 36 …"). When it
has any, they replace what our "AMI0xx-…-VGM,SI,INV,PL.xlsx" says for:
  - the shipment details: vessel/voyage, destination, booking, party, the
    containers with seal and tare (Claude reads the text, once per set of file
    versions, cached in ai_reads);
  - the document-number check (SI/VGM/CI/PL numbers, "036/AMI/X/26");
  - the reference the drafts are compared against (doc_checks.content_check).
An empty Dok folder (a new shipment) keeps the workbook's values.
"""

from __future__ import annotations

import io
import json
import re

from . import doc_checks, doc_status

EXPORTERS = {"AMI", "PMA"}

_PROMPT = """These are a buyer's own shipping documents for ONE export shipment
(shipping instruction, VGM, commercial invoice, packing list), as text, each
headed by its file name and the date it was added. When there are several
versions of the same document, the newest one (latest date, or marked "Rev")
counts. Read:
- vessel and voyage, separately, without a "V." prefix ("MTT REYA V.26RY147N" ->
  vessel "MTT REYA", voyage "26RY147N")
- pod: the port of discharge, and country
- booking: the booking reference
- quantity and size of the containers as written ("4 X 40'HC" -> 4, "40'HC")
- containers: every container number with its seal and tare weight in kg (VGM)
- numbers: the document number of each document type found — SI, VGM, CI, PL
  (e.g. "036/AMI/X/26"); null when a document type is absent
Null for anything not shown.

Documents:
{text}"""

_SCHEMA = {
    "type": "object",
    "properties": {
        **{k: {"type": ["string", "null"]} for k in (
            "vessel", "voyage", "pod", "country", "booking", "size")},
        "quantity": {"type": ["integer", "null"]},
        "containers": {"type": "array", "items": {
            "type": "object",
            "properties": {"number": {"type": "string"}, "seal": {"type": ["string", "null"]},
                           "tare_kg": {"type": ["integer", "null"]}},
            "required": ["number", "seal", "tare_kg"], "additionalProperties": False}},
        "numbers": {"type": "object", "properties": {
            k: {"type": ["string", "null"]} for k in ("SI", "VGM", "CI", "PL")},
            "required": ["SI", "VGM", "CI", "PL"], "additionalProperties": False},
    },
    "required": ["vessel", "voyage", "pod", "country", "booking", "size", "quantity",
                 "containers", "numbers"],
    "additionalProperties": False,
}


def _text(data: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def _ask(text: str) -> dict:
    import anthropic

    resp = anthropic.Anthropic().beta.messages.create(
        model=doc_checks.MODEL, max_tokens=4000,
        betas=["server-side-fallback-2026-06-01"], fallbacks=[{"model": "claude-opus-4-8"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": _PROMPT.format(text=text)}],
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"AI read stopped: {resp.stop_reason}")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def apply(db, folder, exporter: str, fields, ask=_ask, fetch=None):
    """Overwrite `fields` with what the buyer's documents say (AMI / PMA only).
    Returns `fields`; unchanged when there are no buyer documents or they can't
    be read now (no key / budget — the workbook's values stand until then)."""
    ex = (exporter or "").upper()
    if ex not in EXPORTERS:
        return fields
    files = sorted((f for f in doc_status.list_files(folder)
                    if f.folder == f"dok_{ex.lower()}" and f.name.lower().endswith(".pdf")),
                   key=lambda f: f.created)
    if not files:
        return fields
    fetch = fetch or doc_status._fetcher(folder)
    try:
        bundle = "\n\n".join(f"=== file: {f.name} (added {f.created[:10]})\n{_text(fetch(f.ref))}"
                             for f in files)
    except Exception:      # noqa: BLE001 - unreadable now: keep the workbook's values
        return fields
    d = doc_checks._cached(db, "buyer:" + ",".join(f.ref for f in files),
                           ",".join(f.modified for f in files),
                           lambda: ask(bundle), ask is _ask)
    fields.reference_text = bundle            # the drafts are checked against these
    if not d:
        return fields
    fields.vessel_name = d.get("vessel") or fields.vessel_name
    fields.voyage = d.get("voyage") or fields.voyage
    # "CHENNAI PORT" -> "Chennai", as the workbook has it (a changed destination re-seeds items)
    pod = re.sub(r"(?i)\s+port$", "", (d.get("pod") or "").strip())
    fields.destination_port = pod.title() or fields.destination_port
    fields.destination_country = (d.get("country") or "").title() or fields.destination_country
    fields.booking_number = d.get("booking") or fields.booking_number
    fields.container_quantity = d.get("quantity") or fields.container_quantity
    fields.container_size_short = d.get("size") or fields.container_size_short
    conts = [c for c in d.get("containers") or [] if c.get("number")]
    if conts:
        fields.containers = [c["number"].upper().replace(" ", "") for c in conts]
        fields.vgm_containers_read = True
        fields.vgm_rows = {c["number"].upper().replace(" ", ""): {
            "seal": c.get("seal") or "", "tare": str(c["tare_kg"]) if c.get("tare_kg") else ""}
            for c in conts}
    nums = d.get("numbers") or {}
    # The buyer's VGM carries no number; their SI does (the same one) — then the
    # SI is what the CI and PL are compared against.
    ref = ("VGM", nums.get("VGM")) if nums.get("VGM") else ("SI", nums.get("SI"))
    docs = [(f"{ref[0]} (dok buyer)", "vgm", ref[1])] if ref[1] else []
    docs += [(f"{k} (dok buyer)", kind, nums.get(k)) for k, kind in (("CI", "inv"), ("PL", "pl"))
             if nums.get(k)]
    if docs:
        fields.doc_numbers = {"docs": docs}
    return fields
