"""ntfy push channel (2026-08-06).

Request shape, the disabled short-circuit, and the never-raises guarantee.
`requests.post` is monkeypatched, so nothing leaves the machine.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched

import requests

from bot_kalung.core.constants import NTFY_TOPIC
from bot_kalung.core.db import Database, db_path_for
from bot_kalung.core.settings import Settings
from bot_kalung.services import ntfy

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


class FakeResponse:
    def __init__(self, ok=True):
        self.ok = ok


calls = []


def fake_post(url, data=None, headers=None, timeout=None):
    calls.append({"url": url, "data": data, "headers": headers or {},
                  "timeout": timeout})
    return FakeResponse(True)


_real_post = requests.post

with tempfile.TemporaryDirectory() as tmp:
    db = Database(db_path_for(Path(tmp)))
    db.initialize()
    settings = Settings(db)
    settings.set("ntfy_enabled", True)
    settings.set("ntfy_server", "https://ntfy.sh")

    requests.post = fake_post
    try:
        ok = ntfy.publish(settings, "AMJ24: kapal akan berangkat",
                          "Loading sisa 2 · bayar LOLO", kind="departing")
        check("publish returns True on a 2xx", ok is True)
        check("exactly one POST was made", len(calls) == 1)
        call = calls[0]
        check("URL is server/topic",
              call["url"] == f"https://ntfy.sh/{NTFY_TOPIC}")
        check("body carries the message (UTF-8)",
              call["data"] == "Loading sisa 2 · bayar LOLO".encode("utf-8"))
        check("Title header set", call["headers"]["Title"].startswith("AMJ24"))
        check("departing maps to the max priority",
              call["headers"]["Priority"] == "5")
        check("departing is tagged 'warning'",
              call["headers"]["Tags"] == "warning")
        check("a timeout is set so a hung server can't stall the push",
              call["timeout"] == 10)

        calls.clear()
        settings.set("ntfy_enabled", False)
        ok = ntfy.publish(settings, "x", "y")
        check("disabled short-circuits with no POST", ok is False and calls == [])

        settings.set("ntfy_enabled", True)

        def boom(*args, **kwargs):
            raise requests.RequestException("down")

        requests.post = boom
        check("a network error is swallowed (never raises)",
              ntfy.publish(settings, "x", "y") is False)
    finally:
        requests.post = _real_post

    check("subscribe_url is server + topic",
          ntfy.subscribe_url(settings) == f"https://ntfy.sh/{NTFY_TOPIC}")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ntfy OK - all checks passed.")
