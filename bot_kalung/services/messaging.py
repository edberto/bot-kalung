"""Gmail draft composition (PRD Section 7).

Two deliberate narrowings of the PRD, both decided with the user on 2026-07-20:

* **Email only.** WhatsApp steps are plain checkboxes; the worker sends those
  messages themselves, so no WhatsApp URL is built.
* **Only the two teammate addresses are pre-filled.** Contacts are not
  configured, so the external recipient is typed into Gmail by the worker. What
  the draft saves is the subject and body, which are always the same shape.

Nothing is ever sent automatically — a draft opens for review.
"""

from __future__ import annotations

import re
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import date

from ..core.constants import WORKER_EMAILS
from . import naming

GMAIL_COMPOSE = "https://mail.google.com/mail/u/0/"

# PRD Section 14 — some browsers truncate very long URLs.
MAX_BODY_CHARS = 1800
TRUNCATION_NOTE = "\n\n[Pesan dipotong — salin teks lengkap secara manual]"


class MessagingError(Exception):
    """Carries an Indonesian, user-presentable message."""


@dataclass
class Draft:
    recipients: list[str]
    subject: str
    body: str
    url: str


def build_variables(shipment, settings) -> dict[str, str]:
    """PRD Section 7.2."""
    etd = naming.parse_iso_date(shipment["etd_belawan"])
    exporter = shipment["exporter_code"] or ""
    full_names = settings.get("exporter_full_names") or {}

    vessel = (shipment["vessel_name"] or "").strip()
    voyage = (shipment["voyage"] or "").strip()

    return {
        "exporter": exporter,
        "exporter_full": full_names.get(exporter, exporter),
        "seq": str(shipment["sequence_number"] or ""),
        "booking_no": shipment["booking_number"] or "",
        "vessel": vessel,
        "voyage": voyage,
        "vessel_voyage": f"{vessel} {voyage}".strip(),
        "etd": _format_etd(etd, short=False),
        "etd_short": _format_etd(etd, short=True),
        "destination": shipment["destination_port"] or "",
        "destination_country": shipment["destination_country"] or "",
        "container_qty": str(shipment["container_quantity"] or ""),
        "container_size": shipment["container_size_short"] or "",
        "empty_pickup": shipment["empty_pickup_location"] or "",
        "folder_name": (shipment["folder_path"] or "").rstrip("/\\").split("\\")[-1],
    }


def _format_etd(etd: date | None, *, short: bool) -> str:
    if etd is None:
        return ""
    month = naming.MONTHS_EN[etd.month - 1].title()
    if short:
        month = month[:3]
    return f"{etd.day:02d} {month} {etd.year}"


def render(template: str | None, variables: dict[str, str]) -> str:
    """Substitute {placeholders}. Unknown ones are left visible on purpose, so a
    typo in a template shows up instead of silently vanishing.
    """
    if not template:
        return ""

    def replace(match):
        key = match.group(1)
        return variables.get(key, match.group(0))

    return re.sub(r"\{(\w+)\}", replace, template)


def email_recipients(settings) -> list[str]:
    """The two other worker emails.

    PRD 7.1 also put the external contact in the To field, but contacts are not
    configured (user decision, 2026-07-20): the worker types the external
    recipient into Gmail themselves. Only the teammates are pre-filled, which is
    the part that is always the same.
    """
    mine = (settings.get("my_email") or "").strip().lower()
    return [e for e in WORKER_EMAILS if e.lower() != mine]


def build_email_draft(*, to: list[str], subject: str, body: str) -> Draft:
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS - len(TRUNCATION_NOTE)] + TRUNCATION_NOTE
    query = urllib.parse.urlencode(
        {"view": "cm", "to": ",".join(to), "su": subject, "body": body},
        quote_via=urllib.parse.quote)
    return Draft(to, subject, body, f"{GMAIL_COMPOSE}?{query}")


def open_draft(draft: Draft) -> bool:
    try:
        return webbrowser.open(draft.url)
    except webbrowser.Error:
        return False


class DraftBuilder:
    """Renders a template into an openable Gmail draft."""

    def __init__(self, db, settings):
        self.db = db
        self.settings = settings

    def template(self, template_id: str):
        return self.db.query_one(
            "SELECT * FROM message_templates WHERE id=?", (template_id,))

    def build(self, template_id: str, shipment) -> Draft:
        template = self.template(template_id)
        if template is None:
            raise MessagingError(f"Template {template_id} tidak ditemukan.")
        if template["channel"] != "email":
            raise MessagingError(
                f"Template {template_id} bukan template email.")

        variables = build_variables(shipment, self.settings)
        return build_email_draft(
            to=email_recipients(self.settings),
            subject=render(template["subject_template"], variables),
            body=render(template["body_template"], variables))
