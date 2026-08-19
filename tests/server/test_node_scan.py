"""The scan runs over a non-filesystem, Path-like node tree (the Drive-API path).

The live Drive-API scan is validated by hand against the real Drive; this guards
the node abstraction offline with an in-memory fake node — proving discover_series
and scanner.scan need only the small Path surface (name/is_dir/is_file/iterdir),
not a real filesystem.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.services import drive, scanner

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


class FakeNode:
    """The Path-like surface the scan uses, with no filesystem behind it."""

    def __init__(self, name, children=None, is_file=False):
        self._name = name
        self._children = children or []
        self._is_file = is_file

    @property
    def name(self):
        return self._name

    def is_dir(self):
        return not self._is_file

    def is_file(self):
        return self._is_file

    def iterdir(self):
        return list(self._children)

    def __lt__(self, other):
        return self._name < getattr(other, "name", "")


def shipment(seq, code, *, done=False):
    files = [FakeNode(f"{code}{seq:02d}-Karachi-VGM,SI,Inv,PL.xlsx", is_file=True)]
    if done:                                   # an export doc in the send folder
        files.append(FakeNode("Dok kirim", children=[
            FakeNode("NIT-Karachi-INV.pdf", is_file=True)]))
    return FakeNode(f"{seq}.5x40-karachi", children=files)


# NIT: 1 (done), 2, 3 present -> import the contiguous not-done run (2, 3).
nit_year = FakeNode("2026", children=[
    shipment(1, "NIT", done=True), shipment(2, "NIT"), shipment(3, "NIT")])
root = FakeNode("", children=[
    FakeNode("NMEHMOOD & CV.Hassan", children=[nit_year]),
    FakeNode("zzz JANGAN DISENTUH", children=[]),   # DB folder, excluded
])

series = drive.discover_series(root, 2026)
check("discover_series walks a non-filesystem node tree",
      [s.label for s in series] == ["NMEHMOOD & CV.Hassan / 2026"])
check("the excluded DB folder is not a series",
      all("JANGAN" not in s.label for s in series))

plan = scanner.scan(root, 2026, set(), None)
imported = sorted(c.label for c in plan.to_import)
check("scanner imports the contiguous not-done run over nodes",
      imported == ["NIT2", "NIT3"])
check("the done shipment is recognised over nodes",
      any(c.label == "NIT1" for c in plan.done))

# ---- folder references: local path vs Drive id ----------------------------
from bot_kalung.services import drive_api
from bot_kalung.services.tracker import _folder_ref


class RefNode:
    ref = "drive:ABC123"


check("a Drive node stores its id as the folder reference",
      _folder_ref(RefNode()) == "drive:ABC123")
check("a local path stores its filesystem path",
      _folder_ref(Path("/x/y")) == str(Path("/x/y")))
check("is_drive_ref distinguishes a Drive ref from a path",
      drive_api.is_drive_ref("drive:ABC123")
      and not drive_api.is_drive_ref(str(Path("/x/y"))))

node = drive_api.node_from_ref("drive:ABC123", client=None)
check("node_from_ref reconstructs the folder id",
      node.id == "ABC123" and node.is_dir())
check("folder_url builds the Drive web link",
      drive_api.folder_url("drive:ABC123").endswith("/folders/ABC123"))

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Node scan OK - all checks passed.")
