"""Reading draft PDFs (doc automation 1B): title + red review marks, and the
per-file read cache. PDFs are built by hand (no PDF-writing dependency)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.core.db import Database, db_path_for
from bot_kalung.services import doc_content as dc
from bot_kalung.services import doc_status as ds
from bot_kalung.services.doc_status import DocFile

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def make_pdf(title: str, annots: list[str], streams: list[bytes] = (),
             rows: tuple = ()) -> bytes:
    """A one-page PDF showing `title` (plus extra `rows` of (text, y)), with raw
    annotation dicts. Extra stream objects (for /AP appearances) are numbered
    after the annotations."""
    content = " ".join(f"BT /F1 12 Tf 20 {y} Td ({t}) Tj ET"
                       for t, y in ((title, 250),) + tuple(rows)).encode()
    n_annot = len(annots)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources "
         "<< /Font << /F1 4 0 R >> >> /Contents 5 0 R /Annots ["
         + " ".join(f"{6 + i} 0 R" for i in range(n_annot)) + "] >>").encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ] + [a.encode() for a in annots] + [
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 40 20] /Length %d >>\nstream\n"
        % len(s) + s + b"\nendstream" for s in streams]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


RED_DA = "/DA (/F1 12 Tf 1 0.247059 0.247059 rg)"
OK_ONG = f"<< /Type /Annot /Subtype /FreeText /Rect [10 10 60 30] /Contents (OK) /T (ongkalung) /M (D:20261010120000+07'00') {RED_DA} >>"
FIX_VIVO = f"<< /Type /Annot /Subtype /FreeText /Rect [10 40 90 60] /Contents (=== 028N) /T (VivoBook) /M (D:20261010130000+07'00') {RED_DA} >>"
ARROW = "<< /Type /Annot /Subtype /Line /Rect [0 0 50 50] /L [0 0 40 40] /C [1 0 0] /T (edber) /M (D:20261010110000+07'00') >>"
BLACK = "<< /Type /Annot /Subtype /FreeText /Rect [10 70 60 90] /Contents (note) /T (x) /M (D:20261010140000+07'00') /DA (/F1 12 Tf 0 0 0 rg) >>"

# ---- dates, colour, title -------------------------------------------------
check("PDF dates convert to UTC", dc.pdf_date("D:20260929103501+07'00'") == "2026-09-29T03:35:01")
check("red / not-red colours", dc._is_red([1, 0, 0]) and dc._is_red(("1.0", "0.078", "0.094"))
      and not dc._is_red([0, 0, 0]) and not dc._is_red(None))

c = dc.read_pdf(make_pdf("FUMIGATION CERTIFICATE", [OK_ONG]))
check("a fumigation draft is recognised by its title", c.title_kind == "fumi")
check("a red typed 'OK' is an approval mark",
      [(m.kind, m.author, m.when) for m in c.marks] == [("ok", "ongkalung", "2026-10-10T05:00:00")])
check("BILL OF LADING title is recognised",
      dc.read_pdf(make_pdf("BILL OF LADING PROFORMA - NON NEGOTIABLE", [])).title_kind == "bl")

# edber's editor stores the red only in the drawn appearance (AP), not in DA.
ap_ok = ("<< /Type /Annot /Subtype /FreeText /Rect [10 10 60 30] /Contents (OK) /T (edber) "
         "/M (D:20261010150000+07'00') /DA (/Helvetica 14 Tf) /AP << /N 7 0 R >> >>")
c = dc.read_pdf(make_pdf("PEB", [ap_ok], [b"1 1 1 RG 1.000000 0.078431 0.094118 rg"]))
check("red found in the appearance stream when DA has no colour",
      [m.kind for m in c.marks] == ["ok"])

c = dc.read_pdf(make_pdf("COO", [ARROW, OK_ONG, FIX_VIVO, BLACK]))
check("only red marks are kept (a black note is ignored)", len(c.marks) == 3)
check("newest red mark decides: a correction after the OK -> revision",
      dc.review(c.marks).kind == "correction" and dc.review(c.marks).author == "VivoBook")
check("an OK after the corrections -> approval",
      dc.review(dc.read_pdf(make_pdf("COO", [ARROW, OK_ONG])).marks).kind == "ok")
check("an unreadable PDF reads as None, not an error", dc.read_pdf(b"%PDF-1.4 broken") is None)

# ---- COO: the departure-date update is expected, not a revision ----------
ROWS = (("Departure date (as far as known) SEPTEMBER 13, 2026", 200), ("INTEGRA V.186E", 150))
def free(text, rect, minute):
    return (f"<< /Type /Annot /Subtype /FreeText /Rect [{rect}] /Contents ({text}) /T (ongkalung) "
            f"/M (D:202610101{minute}00+07'00') {RED_DA} >>")
ETD_FIX = free("14", "20 195 60 210", "10")                  # typed over the date row
ARROW_TO_DATE = ("<< /Type /Annot /Subtype /Line /Rect [60 195 120 260] /L [120 260 60 200] "
                 "/C [1 0 0] /T (ongkalung) /M (D:20261010111500+07'00') >>")
VESSEL_FIX = free("MAO GANG GUANG ZHOU", "20 145 160 160", "20")   # on the vessel row
coo_etd = dc.read_pdf(make_pdf("COO", [ETD_FIX, ARROW_TO_DATE], rows=ROWS)).marks
check("a typed mark on the departure-date row is flagged",
      [m.on_date for m in coo_etd if m.text] == [True])
check("COO: only the ETD update (and its arrow) -> no revision",
      dc.review(coo_etd, "coo") is None)
check("...but for any other document the same marks are a revision",
      dc.review(coo_etd).kind == "correction")
coo_real = dc.read_pdf(make_pdf("COO", [ETD_FIX, VESSEL_FIX], rows=ROWS)).marks
check("COO: a correction elsewhere (vessel row) is still a revision",
      dc.review(coo_real, "coo").kind == "correction"
      and dc.review(coo_real, "coo").text == "MAO GANG GUANG ZHOU")
coo_ok = dc.read_pdf(make_pdf("COO", [ETD_FIX, free("OK", "200 100 230 115", "30")], rows=ROWS)).marks
check("COO: ETD update then OK -> approval", dc.review(coo_ok, "coo").kind == "ok")
check("marks survive the cache round-trip",
      dc.marks_from_json(dc.marks_to_json(coo_real)) == tuple(coo_real))

# ---- in the status rules: title finds the draft, marks set the stage -------
def info(title=None, kind=None, by=None):
    marks = (dc.Mark(kind, by, "2026-10-10T00:00:00", "OK" if kind == "ok" else "x"),) if kind else ()
    return {"title_kind": title, "marks": marks}


reads = {"0742CN.pdf": info("fumi", "ok", "edber"),
         "2334357090-x.PDF": info("bl"),
         "PEB 21102026.pdf": info(None, "correction", "VivoBook")}
content = lambda f: reads.get(f.name)
D = lambda name, created="2026-10-01T00:00:00": DocFile("draft", name, "r:" + name, created)

inf = ds.infer("fumi", [D("0742CN.pdf")], "AMI", {6}, content)
check("a fumi draft found by title only, marked OK -> Draft OK with who",
      inf.status == "draft_ok" and inf.detail == "OK oleh edber")
check("a BL draft found by title -> Draft diterima",
      ds.infer("bl", [D("2334357090-x.PDF")], "AMJ", {27}, content).status == "draft_received")
check("red corrections -> Draf direvisi",
      ds.infer("peb_npe", [D("PEB 21102026.pdf")], "NIT", {21}, content).status == "draft_revision")
check("only the newest draft counts (a newer unmarked draft is just received)",
      ds.infer("peb_npe", [D("PEB 21102026.pdf", "2026-10-01T00:00:00"),
                           D("PEB 21102026 b.pdf", "2026-10-02T00:00:00")],
               "NIT", {21}, content).status == "draft_received")
check("a final file still wins over a draft's marks",
      ds.infer("peb_npe", [D("PEB 21102026.pdf"),
                           DocFile("peb", "NIT21-PEB ttd.pdf", "r:t", "2026-10-03T00:00:00")],
               "NIT", {21}, content).status == "final")

# ---- the read cache: download once, again only after a change -------------
with tempfile.TemporaryDirectory() as tmp:
    db = Database(db_path_for(Path(tmp)))
    db.initialize()
    pdf = make_pdf("FUMIGATION CERTIFICATE", [OK_ONG])
    calls = []
    reader = ds.DraftReader(db, lambda ref: calls.append(ref) or pdf)
    f = DocFile("draft", "0742CN.pdf", "drive:abc", "2026-10-01T00:00:00", "m1")
    first = reader(f)
    check("the first read downloads and parses", calls == ["drive:abc"]
          and first["title_kind"] == "fumi" and [m.kind for m in first["marks"]] == ["ok"])
    ds.DraftReader(db, lambda ref: calls.append(ref) or pdf)(f)
    check("an unchanged draft is served from the cache (no download)", calls == ["drive:abc"])
    ds.DraftReader(db, lambda ref: calls.append(ref) or pdf)(
        DocFile("draft", "0742CN.pdf", "drive:abc", "2026-10-01T00:00:00", "m2"))
    check("a modified draft is downloaded again", calls == ["drive:abc", "drive:abc"])
    check("non-draft or non-PDF files are never downloaded",
          reader(DocFile("kirim", "x.pdf", "drive:z", "", "m")) is None
          and reader(DocFile("draft", "x.docx", "drive:y", "", "m")) is None)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Doc content OK - all checks passed.")
