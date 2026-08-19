"""Resolving a container's photo folder under the shipment's "Foto" directory.

The shipment-detail container cards get an "open photo folder" button that is
enabled only when a subfolder under "Foto" carries the container number in its
name. This checks that matching (case-insensitive, substring, file-safe).
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.ui.containers_panel import _foto_root, match_photo_dir, photo_dirs

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "shipment"
    root.mkdir()

    check("missing / None folder is safe",
          photo_dirs(None) == {} and photo_dirs(root / "nope") == {})
    check("no Foto folder yields no dirs", photo_dirs(root) == {})

    foto = root / "Foto"
    foto.mkdir()
    (foto / "CMAU8513405").mkdir()
    (foto / "1. TRHU5986693 - reefer").mkdir()
    (foto / "notes.txt").write_text("x", encoding="utf-8")

    dirs = photo_dirs(root)
    check("only subfolders are listed (loose file ignored)", len(dirs) == 2)
    check("exact container-number folder matches",
          match_photo_dir("CMAU8513405", dirs) == foto / "CMAU8513405")
    check("container number as a substring matches",
          match_photo_dir("TRHU5986693", dirs)
          == foto / "1. TRHU5986693 - reefer")
    check("matching is case-insensitive",
          match_photo_dir("cmau8513405", dirs) == foto / "CMAU8513405")
    check("an unknown container has no folder",
          match_photo_dir("ZZZU0000000", dirs) is None)
    check("a blank container has no folder", match_photo_dir("", dirs) is None)

    # The "Foto" root is found case-insensitively / by substring.
    root2 = Path(tmp) / "shipment2"
    root2.mkdir()
    (root2 / "FOTO Kontainer").mkdir()
    (root2 / "FOTO Kontainer" / "MSCU1112223").mkdir()
    check("Foto root matched case-insensitively",
          _foto_root(root2) == root2 / "FOTO Kontainer")
    check("photo folder resolves under that root",
          match_photo_dir("MSCU1112223", photo_dirs(root2))
          == root2 / "FOTO Kontainer" / "MSCU1112223")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Photo folder OK - all checks passed.")
