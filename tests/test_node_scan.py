"""The scan runs over a non-filesystem, Path-like node tree (the Drive-API path).

The live Drive-API scan is validated by hand against the real Drive; this guards
the node abstraction offline with an in-memory fake node — proving discover_series
and scanner.scan need only the small Path surface (name/is_dir/is_file/iterdir),
not a real filesystem.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Node scan OK - all checks passed.")
