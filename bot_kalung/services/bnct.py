"""BNCT vessel monitoring (PRD Section 15, built 2026-07-21).

The BNCT single web portal exposes three unauthenticated monitoring endpoints
straight from its login page (`portal.bnct-id.com/sso/`). Two matter here:

    POST /sso/monitoring?do=getVesselScheduleDetails&key={site}
    POST /sso/monitoring?do=getVesselAlongsideDetails&key={site}

Each needs an `X-CSRF-TOKEN` header whose value is the `csrfTokenForm` hidden
field on the login page, plus that page's session cookie. Each returns an HTML
fragment: one card per vessel. `site` is `ptp` or `tpkb`; a vessel can be at
either, so both are always queried.

A vessel moves through two phases:

* **Schedule** — announced but not yet berthed. Card carries ETB, ETD, Open
  Billing, Open Stack, closing times.
* **Alongside** — at the berth, being worked. Card carries ATB, ETD, berth,
  and a Loading/Discharge/Restow x Plan/Actual/Remain matrix. The last column
  of each row is the Total across container sizes. "Loading Remain Total < 5"
  is the PRD's departure signal.

Parsing is deliberately tolerant: the portal is third-party HTML that can
change without notice, so a parse miss yields an empty list (reported as "not
found yet"), never an exception that could stall monitoring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser

PORTAL_BASE = "https://portal.bnct-id.com/sso"
LOGIN_URL = f"{PORTAL_BASE}/"
MONITORING_URL = f"{PORTAL_BASE}/monitoring"
SITES = ("ptp", "tpkb")

# PRD 15: below this many containers left to load, the vessel is departing.
DEPARTURE_THRESHOLD = 5


class BnctError(Exception):
    """A monitoring fetch failed; carries an Indonesian, user-facing message."""


@dataclass
class BnctVessel:
    """One vessel card parsed from a monitoring fragment."""
    site: str
    phase: str                       # "schedule" | "alongside"
    name: str
    voyage_in: str = ""
    voyage_out: str = ""
    agent: str = ""

    # schedule phase
    etb: str = ""
    etd: str = ""
    open_billing: str = ""
    open_stacking: str = ""
    clossing: str = ""              # the portal's spelling (double-s)
    clossing_reefer: str = ""

    # alongside phase (Total column of each matrix row)
    atb: str = ""
    berth: str = ""
    loading_plan: int | None = None
    loading_actual: int | None = None
    loading_remain: int | None = None
    discharge_plan: int | None = None
    discharge_actual: int | None = None
    discharge_remain: int | None = None
    restow_plan: int | None = None
    restow_actual: int | None = None
    restow_remain: int | None = None

    @property
    def departing(self) -> bool:
        return (self.phase == "alongside" and self.loading_remain is not None
                and self.loading_remain < DEPARTURE_THRESHOLD)

    @staticmethod
    def _pct(done: int | None, plan: int | None) -> int | None:
        if plan is None or done is None or plan <= 0:
            return None
        return round(max(0, min(done, plan)) / plan * 100)

    @property
    def loading_pct(self) -> int | None:
        """How much loading is done, as a percentage of the plan."""
        return self._pct(self.loading_actual, self.loading_plan)

    @property
    def discharge_pct(self) -> int | None:
        return self._pct(self.discharge_actual, self.discharge_plan)

    @property
    def restow_pct(self) -> int | None:
        return self._pct(self.restow_actual, self.restow_plan)


@dataclass
class BnctReading:
    """The monitoring result for one shipment at one point in time."""
    found: bool
    phase: str | None                # None | "schedule" | "alongside"
    checked_at: str
    vessel: BnctVessel | None = None
    note: str = ""

    @property
    def departing(self) -> bool:
        return bool(self.vessel and self.vessel.departing)


# -- HTML token extraction ---------------------------------------------------

class _TextCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tokens: list[str] = []

    def handle_data(self, data: str):
        text = data.strip()
        if text:
            self.tokens.append(re.sub(r"\s+", " ", text))


def _tokens(html: str) -> list[str]:
    parser = _TextCollector()
    try:
        parser.feed(html)
    except Exception:      # malformed third-party HTML must never raise
        pass
    return parser.tokens


def _split_voyage(token: str) -> tuple[str, str]:
    """Split the inbound and outbound voyage.

    'Voyage 26RY123S - 26RY123N' -> ('26RY123S', '26RY123N').

    The two are separated by ' - ' (spaces around the hyphen), but a voyage
    number can itself contain a hyphen — e.g. 'Voyage 0798-087S - 0798-087N' is
    inbound '0798-087S' and outbound '0798-087N'. So split on the *spaced*
    separator, not the first hyphen, or the outbound comes out as
    '087S - 0798-087N'.
    """
    value = re.sub(r"^voyage\s*", "", token, flags=re.IGNORECASE).strip()
    parts = re.split(r"\s+-\s+", value, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return value, value


def _int(token: str) -> int | None:
    try:
        return int(token.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


# -- parsing -----------------------------------------------------------------

def _vessel_starts(tokens: list[str]) -> list[int]:
    """Indices of 'Voyage ...' tokens; the name is the token just before."""
    return [i for i, t in enumerate(tokens)
            if t.lower().startswith("voyage") and i > 0]


def parse_schedule(html: str, site: str) -> list[BnctVessel]:
    tokens = _tokens(html)
    vessels: list[BnctVessel] = []
    starts = _vessel_starts(tokens)
    for n, i in enumerate(starts):
        end = starts[n + 1] - 1 if n + 1 < len(starts) else len(tokens)
        voy_in, voy_out = _split_voyage(tokens[i])
        v = BnctVessel(site=site, phase="schedule", name=tokens[i - 1].strip(),
                       voyage_in=voy_in, voyage_out=voy_out)
        if i + 1 < end and ":" not in tokens[i + 1]:
            v.agent = tokens[i + 1].strip()
        for j in range(i + 1, end):
            label = tokens[j].rstrip(":").strip().lower()
            value = tokens[j + 1].strip() if j + 1 < end else ""
            if label == "etb":
                v.etb = value
            elif label == "etd":
                v.etd = value
            elif label == "open billing":
                v.open_billing = value
            elif label == "open stack":
                v.open_stacking = value
            elif label == "clossing":
                v.clossing = value
            elif label == "clossing reefer":
                v.clossing_reefer = value
        vessels.append(v)
    return vessels


# Matrix rows in the alongside card, each followed by Plan/Actual/Remain lines
# of seven numbers (20/40/45 x FCL/MTY + Total). Only the Total is kept.
_MATRIX_ROWS = ("loading", "discharge", "restow")


def parse_alongside(html: str, site: str) -> list[BnctVessel]:
    tokens = _tokens(html)
    vessels: list[BnctVessel] = []
    starts = _vessel_starts(tokens)
    for n, i in enumerate(starts):
        end = starts[n + 1] - 1 if n + 1 < len(starts) else len(tokens)
        voy_in, voy_out = _split_voyage(tokens[i])
        v = BnctVessel(site=site, phase="alongside", name=tokens[i - 1].strip(),
                       voyage_in=voy_in, voyage_out=voy_out)
        if i + 1 < end and ":" not in tokens[i + 1]:
            v.agent = tokens[i + 1].strip()

        window = tokens[i + 1:end]
        for k, tok in enumerate(window):
            label = tok.rstrip(":").strip().lower()
            nxt = window[k + 1].strip() if k + 1 < len(window) else ""
            if label == "atb":
                v.atb = nxt
            elif label == "etd" and not v.etd:
                v.etd = nxt
            elif label in _MATRIX_ROWS:
                _fill_matrix_row(v, label, window[k:])
        vessels.append(v)
    return vessels


def _totals_after(sub_tokens: list[str], sublabel: str) -> int | None:
    """The 7th number following a Plan/Actual/Remain sub-label is the Total."""
    for idx, tok in enumerate(sub_tokens):
        if tok.strip().lower() == sublabel:
            nums = []
            for t in sub_tokens[idx + 1:idx + 8]:
                value = _int(t)
                if value is None:
                    break
                nums.append(value)
            if len(nums) == 7:
                return nums[6]
            return None
    return None


def _fill_matrix_row(v: BnctVessel, row: str, section_tokens: list[str]):
    # Stop the section at the next matrix-row label so Discharge's numbers are
    # not read into Loading, etc.
    stop = len(section_tokens)
    for idx in range(1, len(section_tokens)):
        if section_tokens[idx].strip().lower() in _MATRIX_ROWS:
            stop = idx
            break
    window = section_tokens[:stop]
    plan = _totals_after(window, "plan")
    actual = _totals_after(window, "actual")
    remain = _totals_after(window, "remain")
    setattr(v, f"{row}_plan", plan)
    setattr(v, f"{row}_actual", actual)
    setattr(v, f"{row}_remain", remain)


# -- vessel matching ---------------------------------------------------------

_NAME_PREFIX_RE = re.compile(r"^(mv|m/v|mt|km|km\.|tb)\.?\s+", re.IGNORECASE)


def normalize_name(name: str) -> str:
    if not name:
        return ""
    text = _NAME_PREFIX_RE.sub("", name.strip())
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def normalize_voyage(voyage: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (voyage or "").upper())


def _name_matches(app_name: str, portal_name: str) -> bool:
    a, b = normalize_name(app_name), normalize_name(portal_name)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _voyage_matches(app_voyage: str, portal_voyage: str) -> bool:
    a, b = normalize_voyage(app_voyage), normalize_voyage(portal_voyage)
    if len(a) < 3 or len(b) < 3:      # too short to trust a substring match
        return False
    return a == b or a.endswith(b) or b.endswith(a) or a in b or b in a


def match_vessel(vessels: list[BnctVessel], vessel_name: str,
                 voyage: str) -> BnctVessel | None:
    """Find the vessel whose name AND voyage both match. Alongside wins over
    schedule when the same vessel appears in both (it is the later phase).
    """
    hits = [
        v for v in vessels
        if _name_matches(vessel_name, v.name)
        and (_voyage_matches(voyage, v.voyage_out)
             or _voyage_matches(voyage, v.voyage_in))
    ]
    if not hits:
        return None
    hits.sort(key=lambda v: 0 if v.phase == "alongside" else 1)
    return hits[0]


# -- fetching ----------------------------------------------------------------

class BnctClient:
    """Fetches and parses the BNCT monitoring fragments for both sites.

    Injectable so the monitor and the tests can supply a fake; the real
    implementation talks to the live portal over HTTPS.
    """

    def fetch_vessels(self) -> list[BnctVessel]:      # pragma: no cover - iface
        raise NotImplementedError


_TOKEN_RE = re.compile(
    r'id="csrfTokenForm"[^>]*value="([0-9a-fA-F-]{36})"')


class HttpBnctClient(BnctClient):
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def fetch_vessels(self) -> list[BnctVessel]:
        import requests

        session = requests.Session()
        session.headers.update({
            "User-Agent": "BotKalung/1.0",
            "X-Requested-With": "XMLHttpRequest",
        })
        try:
            login = session.get(LOGIN_URL, timeout=self.timeout)
            login.raise_for_status()
        except requests.RequestException as exc:
            raise BnctError(f"Tidak dapat menghubungi portal BNCT: {exc}") from exc

        match = _TOKEN_RE.search(login.text)
        if not match:
            raise BnctError("Token portal BNCT tidak ditemukan (situs berubah?).")
        token = match.group(1)

        vessels: list[BnctVessel] = []
        for site in SITES:
            vessels += self._fetch_site(session, token, site, "schedule",
                                        "getVesselScheduleDetails", parse_schedule)
            vessels += self._fetch_site(session, token, site, "alongside",
                                        "getVesselAlongsideDetails", parse_alongside)
        return vessels

    def _fetch_site(self, session, token, site, phase, action, parser):
        import requests

        try:
            resp = session.post(
                MONITORING_URL, params={"do": action, "key": site},
                headers={"X-CSRF-TOKEN": token}, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise BnctError(
                f"Gagal mengambil data {phase} BNCT ({site}): {exc}") from exc
        return parser(resp.text, site)


def read_for_shipment(vessels: list[BnctVessel], vessel_name: str, voyage: str,
                      *, now: datetime | None = None) -> BnctReading:
    """Turn a fetched vessel list into a reading for one shipment."""
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    vessel = match_vessel(vessels, vessel_name, voyage)
    if vessel is None:
        return BnctReading(found=False, phase=None, checked_at=stamp,
                           note="Kapal belum terjadwal di BNCT.")
    return BnctReading(found=True, phase=vessel.phase, checked_at=stamp,
                       vessel=vessel)
