"""Container notifications are aggregated per poll: one message listing every
container that reached status >= 50 in that poll, "AMJ30 - GAOU7230290" per line."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ (for sandbox)

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

from bot_kalung.server import container_digest
from bot_kalung.services import bnct

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


check("nothing received -> no notification", container_digest([]) is None)

sid, title, body = container_digest([
    ("AMJ30", "TLLU5855176", "s-amj30"),
    ("AMJ31", "CAIU1234567", "s-amj31"),
    ("AMJ30", "GAOU7230290", "s-amj30"),
])
check("one notification lists every container, one per line, sorted",
      body == "AMJ30 - GAOU7230290\nAMJ30 - TLLU5855176\nAMJ31 - CAIU1234567")
check("the title counts them and names the shipments",
      title == "3 kontainer diterima di BNCT (AMJ30, AMJ31)")
check("mixed shipments -> not tied to one shipment", sid is None)

sid, _, body = container_digest([("HCIT5", "KKFU7997309", "s-hcit5")])
check("a single shipment's containers link to that shipment",
      sid == "s-hcit5" and body == "HCIT5 - KKFU7997309")

# The trigger itself: only the transition INTO >= 50 counts.
check("reaching 50 counts as received", bnct.is_container_done("50"))
check("below 50 never notifies", not bnct.is_container_done("49")
      and not bnct.is_container_done(None))

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Container digest OK - all checks passed.")
