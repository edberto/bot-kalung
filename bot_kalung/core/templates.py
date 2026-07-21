"""Default message templates T01-T16 (PRD Section 7.3).

Bodies are intentionally **empty** and subjects are intentionally just
`{exporter}{seq}` — e.g. "AMJ24" (user decisions, 2026-07-20). Message content
and any further subject wording vary too much per shipment to template. What
the app supplies is the shipment identifier, so a mailbox threads and searches
by shipment, plus the correct teammate recipients.

The WhatsApp rows (T01, T03, T05, T06, T07, T13, T15, T16) are retained as data
only — those steps are plain checkboxes and no message is composed for them.
"""

# (id, step_code, channel, recipient_role, subject_template, body_template)
DEFAULT_TEMPLATES = [
    ("T01", "A2", "whatsapp", "exporter_head", None, ""),

    ("T02", "A3", "email", "shipper",
     "{exporter}{seq}", ""),

    ("T03", "A3", "whatsapp", "shipper", None, ""),

    ("T04", "A4", "email", "shipping_company",
     "{exporter}{seq}", ""),

    ("T05", "A4", "whatsapp", "shipping_company", None, ""),

    ("T06", "B4", "whatsapp", "indra", None, ""),

    ("T07", "B3", "whatsapp", "toni", None, ""),

    ("T08", "B5", "email", "gucimas",
     "{exporter}{seq}", ""),

    ("T09", "B5", "email", "pestcindo",
     "{exporter}{seq}", ""),

    ("T10", "B6", "email", "nanda",
     "{exporter}{seq}", ""),

    ("T11", "C2", "email", "shipping_company",
     "{exporter}{seq}", ""),

    ("T12", "C3", "email", "shipping_company",
     "{exporter}{seq}", ""),

    ("T13", "D3", "whatsapp", "indra", None, ""),

    ("T14", "E1", "email", "shipping_company",
     "{exporter}{seq}", ""),

    ("T15", "E1", "whatsapp", "shipping_company", None, ""),

    ("T16", "E6", "whatsapp", "exporter_head", None, ""),
]

# TODO: confirm with user — PRD Section 7.3 lists "shipper" (T02/T03) as a
# recipient distinct from "shipping company", but Section 13.4 does not define a
# `shipper` contact role. Stored here as its own role pending confirmation.
