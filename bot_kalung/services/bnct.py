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
  of each row is the Total across container sizes. The departure signal is all
  three Remain Totals (Loading, Restow, Discharge) dropping near zero.

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

# Below this many containers left, an operation counts as finished. A vessel is
# departing once Loading, Restow AND Discharge Remaining are all under it.
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
        """Departed once every operation is essentially finished: Loading,
        Restow AND Discharge Remaining are all near zero (same threshold).

        Loading must be known to judge this; an absent restow/discharge row
        means that operation isn't happening, so it doesn't hold departure back
        — but a row that IS present with work left keeps the vessel non-departed
        even when loading is done.
        """
        if self.phase != "alongside" or self.loading_remain is None:
            return False
        remains = (self.loading_remain, self.discharge_remain, self.restow_remain)
        return all(r < DEPARTURE_THRESHOLD for r in remains if r is not None)

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


# Container endpoint sites (uppercase, unlike the lowercase vessel SITES).
CONTAINER_SITES = ("PTP", "TPKB")
# A container is treated as received/done (and the alert fires) once its numeric
# BNCT status reaches this threshold — 50 (GATE IN) and above (51 STACK
# RECEIVING, …). Set to 50 per the user (2026-08).
CONTAINER_DONE_MIN = 50


def is_container_done(code) -> bool:
    """True when a BNCT status code is numeric and >= CONTAINER_DONE_MIN. A None
    or non-numeric code (e.g. an unread container) is not done."""
    try:
        return int(code) >= CONTAINER_DONE_MIN
    except (TypeError, ValueError):
        return False


@dataclass
class BnctContainer:
    """One container row from the BNCT container search (`getContainerList`)."""
    site: str
    container_no: str
    size: str = ""
    type: str = ""
    fcl: str = ""
    status_code: str = ""
    status_text: str = ""
    shipping_line: str = ""
    voyage_ref: str = ""
    vessel_name: str = ""
    voyage_in: str = ""
    voyage_out: str = ""

    @property
    def status(self) -> str:
        return f"{self.status_code}-{self.status_text}".strip("-")

    @property
    def at_stack_receiving(self) -> bool:
        """Container received/done — status 50 (GATE IN) and above."""
        return is_container_done(self.status_code)


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


def etd_to_iso(value: str) -> str | None:
    """BNCT dates read 'DD/MM/YYYY HH:MM' — return the ISO date, or None.

    Used to converge a shipment's stored ETD onto BNCT's (authoritative) schedule
    so every shipment on one voyage shares the vessel's real departure date.
    """
    match = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", value or "")
    if not match:
        return None
    try:
        return datetime(int(match.group(3)), int(match.group(2)),
                        int(match.group(1))).date().isoformat()
    except ValueError:
        return None


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


_MERGE_SCALARS = ("etb", "etd", "open_billing", "open_stacking", "clossing",
                  "clossing_reefer", "atb", "berth")
_MERGE_MATRICES = ("loading", "discharge", "restow")


def combine_vessels(hits: list[BnctVessel]) -> BnctVessel:
    """Merge the entries of one vessel+voyage that appears at both BNCT terminals.

    A vessel can berth at both terminals at once — one handling loading, the
    other discharge — so each operation's numbers are taken from the terminal
    actually performing it (the one with the larger plan). This keeps the
    departure signal (loading remain) tied to the loading terminal instead of
    reading 0 from the discharge-only terminal.
    """
    alongside = [v for v in hits if v.phase == "alongside"]
    primary = alongside[0] if alongside else hits[0]
    merged = BnctVessel(
        site=primary.site, phase="alongside" if alongside else primary.phase,
        name=primary.name, voyage_in=primary.voyage_in,
        voyage_out=primary.voyage_out, agent=primary.agent)
    for field in _MERGE_SCALARS:
        for v in hits:
            if getattr(v, field):
                setattr(merged, field, getattr(v, field))
                break
    for op in _MERGE_MATRICES:
        best = max(hits, key=lambda v: (getattr(v, f"{op}_plan") or -1))
        for part in ("plan", "actual", "remain"):
            setattr(merged, f"{op}_{part}", getattr(best, f"{op}_{part}"))
    return merged


def match_vessel(vessels: list[BnctVessel], vessel_name: str,
                 voyage: str) -> BnctVessel | None:
    """Find the vessel whose name AND voyage both match. Alongside wins over
    schedule; multiple terminal entries for the same vessel are combined.
    """
    hits = [
        v for v in vessels
        if _name_matches(vessel_name, v.name)
        and (_voyage_matches(voyage, v.voyage_out)
             or _voyage_matches(voyage, v.voyage_in))
    ]
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    return combine_vessels(hits)


# -- fetching ----------------------------------------------------------------

class BnctClient:
    """Fetches and parses the BNCT monitoring fragments for both sites.

    Injectable so the monitor and the tests can supply a fake; the real
    implementation talks to the live portal over HTTPS.
    """

    def fetch_vessels(self) -> list[BnctVessel]:      # pragma: no cover - iface
        raise NotImplementedError

    def fetch_containers(self, container_no: str) -> list[BnctContainer]:
        """Default: no container search (fakes that only mock vessels)."""
        return []

    def fetch_containers_batch(
            self, container_numbers) -> dict[str, list[BnctContainer]]:
        """Fetch several containers in one session. Default: nothing."""
        return {no: self.fetch_containers(no) for no in container_numbers}


# -- container search parsing ------------------------------------------------

# The cards call containerDetail(...) from an onclick; the arg list runs to the
# ')' that precedes the attribute's closing quote, so a ')' inside a value (a
# vessel name like "MV.X (021S-021N)") does not cut it short.
_CONTAINER_DETAIL_RE = re.compile(
    r"""containerDetail\s*\((?P<args>.*?)\)\s*["']""", re.DOTALL)


def _split_js_args(text: str) -> list[str]:
    """Split a JS call's argument list, honouring quotes and escapes."""
    args, cur, quote, i = [], "", None, 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\" and i + 1 < len(text):
                cur += text[i + 1]
                i += 2
                continue
            if c == quote:
                quote = None
            else:
                cur += c
        elif c in "'\"":
            quote = c
        elif c == ",":
            args.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    args.append(cur)
    return [a.strip().strip("'\"").strip() for a in args]


def parse_containers(html: str, site: str) -> list[BnctContainer]:
    """Parse containerDetail(...) cards. Tolerant: a miss yields []."""
    results: list[BnctContainer] = []
    for match in _CONTAINER_DETAIL_RE.finditer(html or ""):
        args = _split_js_args(match.group("args"))
        if len(args) < 12:
            continue
        # (id, containerNo, size, type, fcl, statusCode, statusText,
        #  shippingLine, voyageRef, vesselName, voyageIn, voyageOut)
        results.append(BnctContainer(
            site=site, container_no=args[1], size=args[2], type=args[3],
            fcl=args[4], status_code=args[5], status_text=args[6],
            shipping_line=args[7], voyage_ref=args[8], vessel_name=args[9],
            voyage_in=args[10], voyage_out=args[11]))
    return results


def match_container(containers: list[BnctContainer], vessel_name: str,
                    voyage: str) -> BnctContainer | None:
    """The container card whose vessel+voyage match the shipment. A container can
    appear at both terminals (an old voyage and the current one); pick the match.
    Falls back to the sole card when there is exactly one.
    """
    for c in containers:
        if _name_matches(vessel_name, c.vessel_name) and (
                _voyage_matches(voyage, c.voyage_out)
                or _voyage_matches(voyage, c.voyage_in)):
            return c
    return containers[0] if len(containers) == 1 else None


_TOKEN_RE = re.compile(
    r'id="csrfTokenForm"[^>]*value="([0-9a-fA-F-]{36})"')


class HttpBnctClient(BnctClient):
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def _login(self):
        """Open a session and scrape the CSRF token from the login page."""
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
        return session, match.group(1)

    def fetch_vessels(self) -> list[BnctVessel]:
        session, token = self._login()
        vessels: list[BnctVessel] = []
        for site in SITES:
            vessels += self._fetch_site(session, token, site, "schedule",
                                        "getVesselScheduleDetails", parse_schedule)
            vessels += self._fetch_site(session, token, site, "alongside",
                                        "getVesselAlongsideDetails", parse_alongside)
        return vessels

    def fetch_containers(self, container_no: str) -> list[BnctContainer]:
        session, token = self._login()
        return self._containers_for(session, token, container_no)

    def fetch_containers_batch(
            self, container_numbers) -> dict[str, list[BnctContainer]]:
        """One login, then every container at both terminals — the poll path."""
        session, token = self._login()
        return {no: self._containers_for(session, token, no)
                for no in container_numbers}

    def _containers_for(self, session, token, container_no):
        containers: list[BnctContainer] = []
        for site in CONTAINER_SITES:
            containers += self._fetch_container_site(
                session, token, container_no, site)
        return containers

    def _fetch_container_site(self, session, token, container_no, site):
        import requests

        try:
            resp = session.post(
                MONITORING_URL,
                params={"do": "getContainerList", "key": container_no, "site": site},
                headers={"X-CSRF-TOKEN": token}, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise BnctError(
                f"Gagal mengambil data kontainer BNCT ({site}): {exc}") from exc
        return parse_containers(resp.text, site)

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
