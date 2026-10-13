"""SI / VGM / INV / PL checklist (doc automation 1C, step 1 — no AI).

Read from the shipment's main workbook, shown as sub-items under the action
item "SI / VGM / INV / PL" (not for AMI, PMA, CKJ):

  containers  every sheet's container table (VGM, SI, INV..., P.List...) lists
              all the containers, each with its seal; VGM also has the tare;
              and every sheet lists the same containers as the VGM.
  dates       the VGM date and the INV date(s) are complete (with a day —
              "OCTOBER 2026" is not).
  numbers     document numbers follow one pattern, {seq}{month}{year}
              ("33102026" = shipment 33, October 2026; JMI/AMI/PMA/CKJ have
              their own layouts): the seq is the shipment's own and the VGM,
              every INV and every PL agree. SI is exempt. Runs for every
              exporter (AMI/PMA/CKJ get only this check). Needs the shipment's
              numbers, so the scan finishes it (numbers_check).

Only the rows under each sheet's "CONTAINER NO" header are read: a booking
number elsewhere on the sheet can look like a container number
("MESG1184640" inside "MESG11846400").
"""

from __future__ import annotations

import re
from datetime import date, datetime

CONTAINER = re.compile(r"[A-Z]{4}\d{7}")
_CONT_HEADER = re.compile(r"(?i)\bcont(ainer)?\.?\s*no\b")
_SHEET = re.compile(r"(?i)^\s*(vgm|si\b|inv|p\.?\s*list|pl\b)")
_DATE_LABEL = re.compile(r"(?i)^\s*date\s*:?\s*$")
# A complete date: "10 OCTOBER 2026", "10-10-2026", "10/10/26".
_FULL_DATE = re.compile(r"\b\d{1,2}\s*[-/ ]\s*(?:[A-Za-z]{3,}|\d{1,2})\s*[-/ ,]\s*\d{2,4}\b")
SKIP_EXPORTERS = {"AMI", "PMA", "CKJ"}
# These follow the buyer's ordering, not ours: the sequence on their documents is
# the code number in the folder name ("1.PMA02-..." -> 2), not the folder's
# leading number.
OWN_ORDER_EXPORTERS = {"AMI", "PMA"}


def _kind(sheet_name: str) -> str | None:
    m = _SHEET.match(sheet_name)
    if not m:
        return None
    k = m.group(1).lower().replace(" ", "").replace(".", "")
    return {"vgm": "vgm", "si": "si", "inv": "inv"}.get(k, "pl")


def _row(grid, r):
    return {c: v for (rr, c), v in grid.ordered_cells() if rr == r}


def _table(grid):
    """(header_row, container_col, seal_col, tare_col) of the container table."""
    for (r, c), v in grid.ordered_cells():
        if isinstance(v, str) and _CONT_HEADER.search(v):     # "CONTAINER NO" / AMJ's "CONT NO."
            cells = {cc: str(vv).upper() for cc, vv in _row(grid, r).items() if isinstance(vv, str)}
            seal = next((cc for cc, t in cells.items() if "SEAL" in t), None)
            tare = next((cc for cc, t in cells.items() if "TARE" in t), None)
            return r, c, seal, tare
    return None


def _filled(value) -> bool:
    return value is not None and str(value).strip() not in ("", "0", "-")


def _containers(grid, expected: int):
    """[(row, container_no)] under the header, in a window sized to the party."""
    t = _table(grid)
    if t is None:
        return None, []
    header, col, _, _ = t
    out = []
    for r in range(header + 1, header + max(expected, 1) + 15):
        v = grid.cell(r, col)
        m = isinstance(v, str) and CONTAINER.search(v.upper())
        if m:
            out.append((r, m.group(0)))
    return t, out


def _date_value(grid):
    """The value right of the first short "DATE" label (top of the sheet)."""
    for (r, c), v in grid.ordered_cells():
        if isinstance(v, str) and _DATE_LABEL.match(v):
            for cc in range(c + 1, c + 6):
                val = grid.cell(r, cc)
                if _filled(val):
                    return val
            return None
    return None


def _complete(value) -> bool:
    if isinstance(value, (date, datetime)):
        return True
    return bool(_FULL_DATE.search(str(value or "")))


# -- document numbers -------------------------------------------------------
_VGM_INLINE = re.compile(r"(?i)\bVGM\s*-\s*(\d{6,10})\b")       # "NO : VGM-28092026"
_NO_LABEL = {
    "vgm": re.compile(r"(?i)^(vgm\s*)?no\.?\s*:?$"),
    "inv": re.compile(r"(?i)^(inv(oice)?\s*)?no\.?\s*:?$"),
    "pl": re.compile(r"(?i)^((p\.?\s*list|pl|packing\s*list)\s*)?no\.?\s*:?$"),
}
_PREFIX = {"inv": "I-", "pl": "PL-"}
_SEQ_DATE = re.compile(r"^(\d{1,3})(0[1-9]|1[0-2])(20\d\d)$")


def _doc_number(grid, kind: str) -> str | None:
    """The sheet's own document number: "VGM-28092026" written inline on the
    VGM, else the value right of its "NO" / "INV NO" / "P.LIST NO" label."""
    if kind == "vgm":
        for _, v in grid.ordered_cells():
            m = isinstance(v, str) and _VGM_INLINE.search(v)
            if m:
                return f"VGM-{m.group(1)}"
    for (r, c), v in grid.ordered_cells():
        if isinstance(v, str) and _NO_LABEL[kind].match(v.strip()):
            for cc in range(c + 1, c + 5):
                val = grid.cell(r, cc)
                if _filled(val):
                    return str(int(val)) if isinstance(val, float) and val.is_integer() \
                        else str(val).strip()
            return None
    return None


_STD_NUMBER = re.compile(r"(?i)^(?:(VGM|I|INV|PL)-)?(\d{1,3})(0[1-9]|1[0-2])(20\d\d)$")
_ROMAN = {r: i for i, r in enumerate(
    ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"), 1)}


def _parse_number(value: str):
    """A document number -> (style, seq, month, year), or None if unreadable.

    The standard {seq}{month}{year} — plain "33102026" or prefixed
    "VGM-28092026" / "I-..." / "PL-..." — gives its prefix as the style ("" when
    plain). An exporter's own pattern gives style "custom": JMI "27/JMI/092026",
    "PL/JMI/092026/27"; AMI/PMA "031/AMI/IX/26" (Roman month, 2-digit year);
    CKJ "24/INV/CKJ/VIII/2026"."""
    m = _STD_NUMBER.match(value.strip())
    if m:
        return ((m.group(1) or "").upper(), int(m.group(2)), int(m.group(3)), int(m.group(4)))
    month = year = None
    numbers = []
    for token in re.split(r"[^0-9A-Za-z]+", value.strip()):
        if re.fullmatch(r"(0[1-9]|1[0-2])20\d\d", token):            # "092026"
            month, year = int(token[:2]), int(token[2:])
        elif token.upper() in _ROMAN and month is None:
            month = _ROMAN[token.upper()]
        elif re.fullmatch(r"20\d\d", token):
            year = int(token)
        elif token.isdigit() and len(token) <= 3:
            numbers.append(token)
    if month is not None and year is None and len(numbers) >= 2:   # "031/AMI/IX/26"
        year = 2000 + int(numbers.pop())
    if month is None or year is None or not numbers:
        return None
    return ("custom", int(numbers[0]), month, year)


def read_numbers(grids: dict) -> dict:
    """The raw material for the number check, read with the workbook: each VGM /
    INV / PL sheet's document number (VGM first)."""
    sheets = [(name.strip(), _kind(name), g) for name, g in grids.items()
              if _kind(name) in ("vgm", "inv", "pl")]
    sheets.sort(key=lambda s: s[1] != "vgm")
    return {"docs": [(n, k, _doc_number(g, k)) for n, k, g in sheets]}


def numbers_check(raw: dict, own_seqs: set[int]) -> dict:
    """Document numbers follow one pattern, {seq}{month}{year}: the seq is the
    shipment's own (its folder number, or for AMI/PMA the code number in the
    folder/workbook name), and the VGM, every INV and every PL agree on seq,
    month and year. In the standard format INV/PL also follow the VGM's style
    (prefixed VGM-... -> I-... / PL-...; plain -> plain). The month is not
    checked against the invoice date — that's the team's call."""
    notes = []
    docs = raw.get("docs") or []
    vgm = next((d for d in docs if d[1] == "vgm"), None)
    if not vgm or not vgm[2]:
        return {"key": "numbers", "label": "Nomor dokumen", "done": False,
                "notes": ["VGM: nomor kosong"]}
    ref = _parse_number(vgm[2])
    want = {"inv": "I", "pl": "PL"} if ref and ref[0] == "VGM" else {"inv": "", "pl": ""}
    for name, kind, value in docs:
        if not value:
            notes.append(f"{name}: nomor kosong")
            continue
        parsed = _parse_number(value)
        if parsed is None:
            notes.append(f"{name}: nomor {value} tidak dikenali")
            continue
        style, seq, month, year = parsed
        if own_seqs and seq not in own_seqs:
            notes.append(f"{name}: {value} — nomor urut {seq} bukan untuk pengiriman ini")
        if ref and kind != "vgm" and (seq, month, year) != ref[1:]:
            notes.append(f"{name}: {value} — tidak sama dengan VGM ({vgm[2]})")
        if (kind in want and ref and ref[0] != "custom" and style != "custom"
                and style != want[kind]):
            expected = (want[kind] + "-" if want[kind] else "") + f"{seq:02d}{month:02d}{year}"
            notes.append(f"{name}: {value} (seharusnya {expected})")
    return {"key": "numbers", "label": "Nomor dokumen", "done": not notes, "notes": notes}


def compute(grids: dict, party: int | None) -> list[dict]:
    """The checklist: [{"key", "label", "done", "notes": [...]}]."""
    sheets = [(name.strip(), _kind(name), g) for name, g in grids.items() if _kind(name)]
    sheets.sort(key=lambda s: s[1] != "vgm")       # VGM first: it is the reference
    vgm = next((g for _, k, g in sheets if k == "vgm"), None)

    # How many containers to expect: the VGM's numbered rows, else the party.
    expected = party or 0
    if vgm is not None:
        t = _table(vgm)
        if t:
            numbered = 0
            for r in range(t[0] + 1, t[0] + 60):
                idx = vgm.cell(r, 2)
                if isinstance(idx, (int, float)) and not isinstance(idx, bool):
                    numbered += 1
                elif numbered:
                    break
            expected = numbered or expected

    notes, reference, tables = [], None, 0
    for name, kind, g in sheets:
        t, rows = _containers(g, expected)
        if t is None:
            continue
        tables += 1
        _, _, seal_col, tare_col = t
        found = [n for _, n in rows]
        if expected and len(found) < expected:
            notes.append(f"{name}: {len(found)}/{expected} kontainer terisi")
        if seal_col is not None:
            no_seal = [n for r, n in rows if not _filled(g.cell(r, seal_col))]
            if no_seal:
                notes.append(f"{name}: seal kosong ({len(no_seal)})")
        if kind == "vgm":
            reference = set(found)
            if tare_col is not None:
                no_tare = [n for r, n in rows if not _filled(g.cell(r, tare_col))]
                if no_tare:
                    notes.append(f"{name}: tare kosong ({len(no_tare)})")
        elif reference is not None:
            extra = sorted(set(found) - reference)
            if extra:
                notes.append(f"{name}: {', '.join(extra)} tidak ada di VGM")
    if not tables:      # never "done" just because nothing could be read
        notes.append("tabel kontainer tidak ditemukan")
    if not expected:
        notes.append("jumlah kontainer belum diketahui")
    containers = {"key": "containers", "label": "Kontainer, seal & tare",
                  "done": not notes, "notes": notes}

    date_notes = []
    for name, kind, g in sheets:
        if kind not in ("vgm", "inv"):
            continue
        value = _date_value(g)
        if value is None:
            date_notes.append(f"{name}: tanggal kosong")
        elif not _complete(value):
            date_notes.append(f"{name}: tanggal belum lengkap ({str(value).strip()})")
    dates = {"key": "dates", "label": "Tanggal VGM & INV",
             "done": not date_notes, "notes": date_notes}
    return [containers, dates]
