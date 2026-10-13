"""Action-item status from the files in a shipment's Drive folder (doc automation,
Phase 1A: file and folder names only).

Each document leaves recognisable files as it moves along — a draft in Draf/,
the signed or final copy in Dok kirim/ or PEB & NPE/ — so the status can be
inferred from what is in the folder. Rules are the user's (2026-10-13):

  SI/VGM/INV/PL  Dikerjakan until an INV and a PL file are in Dok kirim -> Final.
                 AMI / PMA: Final once their "Dok AMI" / "Dok PMA" folder has a
                 file; CKJ: once PDF/ has a file.
  PEB/NPE        PEB in Draf -> Draft diterima; a "PEB ... ttd" file in
                 PEB & NPE/ -> Final.
  Fumi           fumi in Draf -> Draft diterima; any file in Fumi/ -> Menunggu
                 Hardcopy; fumi in Dok kirim -> Hardcopy diterima.
  Phyto          PHYTO in Draf -> Draft diterima; in Dok kirim -> Final.
  COO            COO in Draf -> Draft diterima; in Dok kirim -> Hardcopy diterima.
  BL             a draft BL in Draf -> Draft diterima; BL/OBL/SWB in Dok kirim
                 -> Final.
  Dok Bank       a Mandiri/BCA/BNI folder -> the item appears; Final once it holds
                 a PDF.

Movement: forward always. Backward only when a newer file starting with REV or
NOTUL (a correction) re-adjusts it. The bot only acts when its inference — the
status AND the file behind it — changes, so a status someone sets by hand stays
until a new file appears. Reading document contents (a fumigation title, a
"BILL OF LADING" title, red OK / correction marks) is Phase 1B.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from ..core.constants import ACTION_STATUSES, DOK_BANK
from . import drive

RANK = {status: i for i, status in enumerate(ACTION_STATUSES)}
_REV_RE = re.compile(r"(?i)^\s*(rev|notul)")


def _word(pattern: str) -> re.Pattern:
    """`pattern` as a whole word: not glued to other letters ("BL" must not hit
    "BLW22931", "COO" must not hit "COOP")."""
    return re.compile(rf"(?i)(?<![a-z])(?:{pattern})(?![a-z])")


INV = _word(r"inv|invoice")
PL = _word(r"pl|p\.?\s?list|packing")
PEB = _word("peb")
TTD = _word("ttd")
PHYTO = re.compile(r"(?i)phyto")
FUMI = re.compile(r"(?i)fumi")
COO = _word("coo")
BL_FINAL = re.compile(r"(?i)(?<![a-z])(?:o?bl|swb)(?![a-z])|copy\s?bl|scan\s?bl")
BL_DRAFT = re.compile(r"(?i)draft?\s?bl|bl\s?draft|_draft|^\d{9,11}-\d{14}")
NOT_INV = re.compile(r"(?i)dhl|(?<![a-z])aca(?![a-z])")   # courier / insurance invoices
BANK = _word(r"mandiri|bca|bni")


@dataclass(frozen=True)
class DocFile:
    folder: str          # folder kind: draft / kirim / peb / pdf / fumi / dok_ami / bank / other / root
    name: str
    ref: str | None      # 'drive:{id}' or a filesystem path — for the PWA link
    created: str         # ISO timestamp the file was added (orders REV/NOTUL)

    @property
    def is_rev(self) -> bool:
        return bool(_REV_RE.match(self.name))


@dataclass(frozen=True)
class Inferred:
    status: str
    file: DocFile | None
    is_rev: bool = False


def folder_kind(name: str) -> str:
    """Classify a shipment subfolder by its (inconsistently spelled) name."""
    if BANK.search(name):
        return "bank"
    s = re.sub(r"[^a-z]", "", name.lower())
    if s.startswith("draf"):
        return "draft"
    if s.startswith("dokkirim"):
        return "kirim"
    if s.startswith("peb"):
        return "peb"
    if s == "pdf":
        return "pdf"
    if s == "fumi":
        return "fumi"
    m = re.fullmatch(r"do[ck]([a-z]+)", s)       # "Dok AMI", "doc ami", "Dok PMA"
    return f"dok_{m.group(1)}" if m else "other"


def _created(node) -> str:
    stamp = getattr(node, "created", None)
    if stamp:
        return stamp
    try:                                      # a local path (tests / desktop scan)
        ts = os.path.getmtime(node)
    except (OSError, TypeError):
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _ref(node) -> str | None:
    ref = getattr(node, "ref", None)
    return ref if ref is not None else str(node)


def list_files(folder) -> list[DocFile]:
    """Files in the shipment folder (kind "root") and one level of subfolders."""
    out = []
    for entry in drive._safe_iterdir(folder):
        if entry.is_dir():
            kind = folder_kind(entry.name)
            out += [DocFile(kind, f.name, _ref(f), _created(f))
                    for f in drive._safe_iterdir(entry) if not f.is_dir()]
        else:
            out.append(DocFile("root", entry.name, _ref(entry), _created(entry)))
    return out


def has_bank_folder(files: list[DocFile]) -> bool:
    return any(f.folder == "bank" for f in files)


# -- per-document candidates --------------------------------------------------
# Each returns [(status, file)] for every file that is evidence for that
# document. REV/NOTUL files that match the document but no stage rule fall back
# to `REV_FALLBACK` (a correction under way).

def _si_vgm(files, exporter):
    ex = exporter.upper()
    if ex in ("AMI", "PMA"):
        return [("final", f) for f in files if f.folder == f"dok_{ex.lower()}"]
    if ex == "CKJ":
        return [("final", f) for f in files if f.folder == "pdf"]
    kirim = [f for f in files if f.folder == "kirim" and not NOT_INV.search(f.name)]
    inv = [f for f in kirim if INV.search(f.name)]
    pl = [f for f in kirim if PL.search(f.name)]
    out = []
    if inv and pl:      # both present: the newer of the two is the evidence
        out.append(("final", max(inv + pl, key=lambda f: f.created)))
    out += [("in_progress", f) for f in files
            if f.is_rev and f.folder in ("pdf", "kirim") and not (inv and pl and f in inv + pl)]
    return out


def _peb(files, exporter):
    out = [("final", f) for f in files if f.folder == "peb" and PEB.search(f.name) and TTD.search(f.name)]
    out += [("draft_received", f) for f in files if f.folder == "draft" and PEB.search(f.name)]
    out += [("draft_received", f) for f in files                       # a NOTUL/REV PEB, unsigned
            if f.is_rev and f.folder == "peb" and PEB.search(f.name) and not TTD.search(f.name)]
    return out


def _fumi(files, exporter):
    out = [("hardcopy_received", f) for f in files if f.folder == "kirim" and FUMI.search(f.name)]
    out += [("waiting_hardcopy", f) for f in files if f.folder == "fumi"]
    out += [("draft_received", f) for f in files if f.folder == "draft" and FUMI.search(f.name)]
    return out


def _phyto(files, exporter):
    out = [("final", f) for f in files if f.folder == "kirim" and PHYTO.search(f.name)]
    out += [("draft_received", f) for f in files if f.folder == "draft" and PHYTO.search(f.name)]
    return out


def _coo(files, exporter):
    out = [("hardcopy_received", f) for f in files if f.folder == "kirim" and COO.search(f.name)]
    out += [("draft_received", f) for f in files if f.folder == "draft" and COO.search(f.name)]
    return out


def _bl(files, exporter):
    out = [("final", f) for f in files if f.folder == "kirim" and BL_FINAL.search(f.name)]
    out += [("draft_received", f) for f in files if f.folder == "draft" and BL_DRAFT.search(f.name)]
    return out


def _dok_bank(files, exporter):
    return [("final", f) for f in files
            if f.folder == "bank" and f.name.lower().endswith(".pdf")]


# A copied folder (HAI39 started as a copy of HAI32's) still carries the old
# shipment's files. A file is foreign when its name names another sequence:
#   "HAI32-Inv.pdf" — the shipment's own exporter code + a different number, or
#   "PEB 32092026"  — the <seq><MM><YYYY> naming used for PEB / COO / Phyto.
_DATED_SEQ = re.compile(r"(?<!\d)(\d{1,3})(0[1-9]|1[0-2])(20\d\d)(?!\d)")


def _foreign(f: DocFile, exporter: str, seqs: set[int]) -> bool:
    if not seqs:
        return False
    if exporter:
        for m in re.finditer(rf"(?i)(?<![a-z]){re.escape(exporter)}-?(\d+)", f.name):
            if int(m.group(1)) not in seqs:
                return True
    m = _DATED_SEQ.search(f.name)
    return bool(m) and int(m.group(1)) not in seqs


RULES = {"si_vgm": _si_vgm, "peb_npe": _peb, "fumi": _fumi, "phyto": _phyto,
         "coo": _coo, "bl": _bl, DOK_BANK[0]: _dok_bank}
# With no evidence at all: SI/VGM is being worked on from the moment the
# shipment exists; everything else says nothing (None).
DEFAULT = {"si_vgm": "in_progress"}


def infer(code: str, files: list[DocFile], exporter: str,
          seqs: set[int] | None = None) -> Inferred | None:
    """The status the folder shows for one document, or None (no evidence).
    `seqs` are the shipment's own numbers (folder + workbook); files naming
    another number are ignored."""
    rule = RULES.get(code)
    if rule is None:
        return None
    files = [f for f in files if not _foreign(f, exporter or "", seqs or set())]
    cands = rule(files, exporter or "")
    normal = [(s, f) for s, f in cands if not f.is_rev]
    revs = [(s, f) for s, f in cands if f.is_rev]
    # Furthest stage the ordinary files reach; ties go to the newest file.
    base = max(normal, key=lambda c: (RANK[c[0]], c[1].created), default=None)
    newest_rev = max(revs, key=lambda c: c[1].created, default=None)
    if newest_rev and (base is None or newest_rev[1].created > base[1].created):
        return Inferred(newest_rev[0], newest_rev[1], is_rev=True)
    if base:
        return Inferred(base[0], base[1])
    if code in DEFAULT:
        return Inferred(DEFAULT[code], None)
    return None


def sync_shipment(items, shipment, folder) -> list[str]:
    """Apply the folder's evidence to one shipment's action items. Returns
    report lines for the statuses it changed. An empty/unreadable folder
    (e.g. trashed) changes nothing."""
    from ..core.constants import ACTION_STATUS_LABELS

    files = list_files(folder)
    if not files:
        return []
    label = f"{shipment['exporter_code']}{shipment['sequence_number']}"
    if has_bank_folder(files):
        items.ensure_item(shipment["id"], *DOK_BANK)
    changes = []
    seqs = own_sequences(shipment, files)
    for it in items.auto_rows(shipment["id"]):
        inferred = infer(it["code"], files, shipment["exporter_code"] or "", seqs)
        if inferred is None:
            continue
        name = inferred.file.name if inferred.file else None
        ref = inferred.file.ref if inferred.file else None
        if (inferred.status, name, ref) == (it["auto_status"], it["auto_evidence"],
                                            it["auto_evidence_ref"]):
            continue                         # nothing new — no write, no realtime event
        new = decide(it["status"], it["auto_status"], it["auto_evidence_ref"], inferred,
                     it["updated_at"] or it["created_at"])
        items.record_auto(it["id"], new, inferred.status, name, ref)
        if new:
            changes.append(f"{label}: {it['title']} -> {ACTION_STATUS_LABELS[new]}"
                           f" ({name or 'tanpa file'})")
    return changes


def _utc19(stamp: str | None) -> str:
    """Comparable 'YYYY-MM-DDTHH:MM:SS' (Drive's createdTime and the app's
    timestamps are both UTC)."""
    return (stamp or "").replace(" ", "T")[:19]


def decide(current: str, auto_status: str | None, auto_ref: str | None,
           inferred: Inferred, last_change: str | None = None) -> str | None:
    """The status to write, or None to leave the item alone.

    Nothing happens unless the inference (status + evidence file) changed since
    the bot last acted — so a manual edit stays until a new file appears. Then:
    forward moves always apply; a backward move only when caused by a REV/NOTUL
    correction file that appeared after the item's last status change (an old
    REV doesn't undo a status someone set later)."""
    ref = inferred.file.ref if inferred.file else None
    if (inferred.status, ref) == (auto_status, auto_ref):
        return None
    if inferred.status == current:
        return None
    if RANK[inferred.status] > RANK.get(current, 0):
        return inferred.status
    if inferred.is_rev and _utc19(inferred.file.created) > _utc19(last_change):
        return inferred.status
    return None


def own_sequences(shipment, files: list[DocFile]) -> set[int]:
    """The shipment's own numbers: its folder sequence plus the main workbook's
    (MA2's workbook is MA027, so its files say 027)."""
    seqs = {int(shipment["sequence_number"])} if shipment["sequence_number"] else set()
    for f in files:
        if f.folder == "root" and drive.is_main_workbook(f.name):
            seq = drive.main_workbook_sequence(f.name)
            if seq is not None:
                seqs.add(seq)
    return seqs
