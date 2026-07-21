"""Workflow checklist and messaging (PRD Sections 6.2, 6.3, 6.4, 7)."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox  # noqa: F401 - keeps the real bootstrap pointer untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from bot_kalung.core.constants import (
    STEP_ACTIONS, STEPS_COMPLETED_BY_ACTION, WORKER_EMAILS, WORKFLOW_STEPS,
)
from bot_kalung.core.context import AppContext
from bot_kalung.services import messaging
from bot_kalung.services.messaging import DraftBuilder, MessagingError
from bot_kalung.services.shipments import Shipments
from bot_kalung.ui import shipment_detail
from bot_kalung.ui.main_window import MainWindow

# Action buttons open real browser windows; record the calls instead so running
# the tests does not spray tabs across the machine.
opened: list[tuple[str, str]] = []
shipment_detail.whatsapp.open_whatsapp = lambda: (
    opened.append(("whatsapp", "")) or (True, "stub"))
shipment_detail.open_path_or_url = lambda url: (
    opened.append(("url", url)) or True)
shipment_detail.messaging.open_draft = lambda draft: (
    opened.append(("email", draft.url)) or True)

failures = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


app = QApplication.instance() or QApplication([])

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    root = tmp / "Drive"
    (root / "AMJ").mkdir(parents=True)
    folder = root / "AMJ" / "2026" / "24.3x40'-karachi-2331048250-Integra 181E-25 jul"
    folder.mkdir(parents=True)
    (folder / "AMJ24-VGM,SI,Inv,PL.xls").write_text("x", encoding="utf-8")

    ctx = AppContext()
    ctx.create(root)
    ctx.settings.set_many({"setup_complete": "1", "my_email": WORKER_EMAILS[0]})

    shipments = Shipments(ctx.db)
    shipment_id = shipments.create({
        "exporter_code": "AMJ", "sequence_number": 24,
        "booking_number": "2331048250", "vessel_name": "INTEGRA", "voyage": "181E",
        "etd_belawan": "2026-07-25", "destination_port": "KARACHI",
        "destination_country": "PAKISTAN", "container_quantity": 3,
        "container_size_short": "40'",
        "empty_pickup_location": "PT Samudera Sarana Logistik",
        "quarantine_required": True, "folder_path": str(folder),
        "shipping_company": "Evergreen",
    })

    # The wizard marks these on a successful create (it is the only code that
    # knows the folder and Excel actually succeeded); mirror that here.
    shipments.set_step(shipment_id, "A1", True, source="auto")
    shipments.set_step(shipment_id, "B1", True, source="auto")

    row = shipments.get(shipment_id)

    # ---- template variables (PRD 7.2) ----------------------------------
    variables = messaging.build_variables(row, ctx.settings)
    check("exporter variable", variables["exporter"] == "AMJ")
    check("seq variable", variables["seq"] == "24")
    check("booking variable", variables["booking_no"] == "2331048250")
    check("vessel_voyage combined", variables["vessel_voyage"] == "INTEGRA 181E")
    check("etd long form", variables["etd"] == "25 July 2026")
    check("etd short form", variables["etd_short"] == "25 Jul 2026")
    check("container qty and size",
          variables["container_qty"] == "3" and variables["container_size"] == "40'")
    check("folder name variable",
          variables["folder_name"].startswith("24.3x40'"))

    check("placeholders substituted",
          messaging.render("Booking {booking_no} / {vessel_voyage}", variables)
          == "Booking 2331048250 / INTEGRA 181E")
    check("unknown placeholder left visible",
          messaging.render("{nope}", variables) == "{nope}")
    check("empty template renders empty", messaging.render(None, variables) == "")

    # ---- recipients (PRD 7.1, narrowed 2026-07-20) -----------------------
    to = messaging.email_recipients(ctx.settings)
    check("my own email excluded", WORKER_EMAILS[0] not in to)
    check("other two workers included",
          WORKER_EMAILS[1] in to and WORKER_EMAILS[2] in to)
    check("exactly the two teammates", len(to) == 2)

    # ---- drafts ----------------------------------------------------------
    builder = DraftBuilder(ctx.db, ctx.settings)

    email = builder.build("T10", row)   # Nanda
    check("only the two teammates are pre-filled",
          set(email.recipients) == {WORKER_EMAILS[1], WORKER_EMAILS[2]})
    check("no external recipient is guessed",
          "nanda@example.com" not in email.recipients)
    check("email subject rendered",
          "AMJ" in email.subject and "24" in email.subject)
    check("gmail compose url", email.url.startswith(
        "https://mail.google.com/mail/u/0/?view=cm"))
    check("subject present in the query", "su=" in email.url)

    check("body is left empty for the worker to write", email.body == "")

    # Every email template builds, carries the teammates, and has a subject.
    broken = []
    for template_id in ("T02", "T04", "T08", "T09", "T10", "T11", "T12", "T14"):
        draft = builder.build(template_id, row)
        if len(draft.recipients) != 2 or not draft.subject.strip():
            broken.append(template_id)
        if "{" in draft.subject:  # unsubstituted
            broken.append(f"{template_id} (unsubstituted placeholder)")
    check(f"all email templates build with a subject {broken or ''}", not broken)

    try:
        builder.build("T01", row)       # a WhatsApp template
        check("whatsapp templates are rejected", False)
    except MessagingError as exc:
        check("whatsapp templates are rejected", "email" in str(exc).lower())

    long_body = messaging.build_email_draft(
        to=["a@b.com"], subject="s", body="x" * 5000)
    check("over-long body truncated", len(long_body.body) <= messaging.MAX_BODY_CHARS)
    check("truncation is signposted", "dipotong" in long_body.body)

    # ---- checklist UI -----------------------------------------------------
    window = MainWindow(ctx)
    window.open_shipment(shipment_id)
    checklist = window.detail.checklist

    check("every step has a row", len(checklist.rows) == len(WORKFLOW_STEPS))
    check("five phase sections", len(checklist.sections) == 5)

    check("A1 auto-completed at creation", checklist.rows["A1"].complete)
    check("B1 auto-completed at creation", checklist.rows["B1"].complete)
    check("A2 starts pending", not checklist.rows["A2"].complete)

    # E4 left phase 2 on 2026-07-20 (user request), so nothing is greyed out.
    check("PDF export is no longer greyed out",
          checklist.rows["E4"].checkbox.isEnabled()
          and not checklist.rows["E4"].phase2_only)
    check("every step is enabled",
          all(r.checkbox.isEnabled() for r in checklist.rows.values()))
    check("E4 offers the export button",
          [a[1] for a in STEP_ACTIONS["E4"]] == ["pdf"])

    check("steps with actions have buttons", len(checklist.rows["B6"].buttons) == 1)

    # Communication buttons are labelled generically — "Email" / "WhatsApp",
    # never by recipient (user, 2026-07-21).
    comm_labels = {label for actions in STEP_ACTIONS.values()
                   for label, kind, _ in actions if kind in ("template", "whatsapp")}
    check(f"communication buttons are just Email/WhatsApp {comm_labels}",
          comm_labels <= {"Email", "WhatsApp"})
    check("B5 has a single Email button now",
          STEP_ACTIONS["B5"] == [("Email", "template", "T08")])
    check("E1 keeps both an Email and a WhatsApp button",
          [k for _, k, _ in STEP_ACTIONS["E1"]] == ["template", "whatsapp"])

    check("B3 is WhatsApp only",
          [a[1] for a in STEP_ACTIONS["B3"]] == ["whatsapp"])
    check("A3 and A4 are WhatsApp only now",
          [a[1] for a in STEP_ACTIONS["A3"]] == ["whatsapp"]
          and [a[1] for a in STEP_ACTIONS["A4"]] == ["whatsapp"])
    check("purely manual steps have no buttons",
          checklist.rows["C1"].buttons == [])

    # A WhatsApp button must never tick its step, even on a step whose email
    # button does (A3 has both).
    window.detail._on_action("B3", "whatsapp", "")
    check("Toni step stays manual",
          not any(s.code == "B3" and s.is_complete
                  for s in shipments.steps(shipment_id)))
    window.detail._on_action("B4", "whatsapp", "")
    check("LOLO payment stays manual",
          not any(s.code == "B4" and s.is_complete
                  for s in shipments.steps(shipment_id)))
    window.detail._on_action("D2", "url", "https://example.com")
    check("portal link does not complete the step",
          not any(s.code == "D2" and s.is_complete
                  for s in shipments.steps(shipment_id)))
    check("whatsapp button actually opened whatsapp",
          ("whatsapp", "") in opened)
    check("portal button actually opened the link",
          ("url", "https://example.com") in opened)

    # An email button does complete its step.
    window.detail._on_action("B6", "template", "T10")
    check("email button completes its step",
          any(s.code == "B6" and s.is_complete
              for s in shipments.steps(shipment_id)))
    check("email button opened a gmail draft",
          any(kind == "email" and "mail.google.com" in url
              for kind, url in opened))

    # manual toggle
    window.detail._on_step_toggled("C1", True)
    check("manual check persists",
          any(s.code == "C1" and s.is_complete
              for s in shipments.steps(shipment_id)))
    check("row reflects completion", checklist.rows["C1"].complete)

    window.detail._on_step_toggled("C1", False)
    check("unchecking is silent and persists",
          not any(s.code == "C1" and s.is_complete
                  for s in shipments.steps(shipment_id)))

    # header progress follows the checklist
    before = window.detail.progress_bar.value()
    window.detail._on_step_toggled("D1", True)
    check("header progress increments",
          window.detail.progress_bar.value() == before + 1)

    # action mapping sanity
    check("every action step exists in the workflow",
          set(STEP_ACTIONS) <= {s[0] for s in WORKFLOW_STEPS})
    check("every auto-completing step has an action",
          STEPS_COMPLETED_BY_ACTION <= set(STEP_ACTIONS))
    check("B4 (LOLO payment) is not auto-completed",
          "B4" not in STEPS_COMPLETED_BY_ACTION)
    check("D3 is not auto-completed by its button",
          "D3" not in STEPS_COMPLETED_BY_ACTION)

    # phase headers count only phase-1 steps
    phase_a = next(s for s in checklist.sections if s.key == "A")
    check("phase header shows a count", "/" in phase_a.header.text())
    phase_a.toggle()
    check("phase collapses", not phase_a.body.isVisible() or not phase_a.expanded)
    phase_a.toggle()
    check("phase expands again", phase_a.expanded)

    # completion banner
    check("complete banner hidden while steps remain",
          not checklist.complete_banner.isVisible())
    # tuple layout: (code, phase, title, description, auto_complete, phase2_only)
    for code, phase, _title, _desc, _auto, phase2_only in WORKFLOW_STEPS:
        if phase == "E" and not phase2_only:
            shipments.set_step(shipment_id, code, True)
    window.detail.load(shipment_id)
    check("complete banner appears once phase E is done",
          checklist.complete_banner.isVisible()
          or not checklist.complete_banner.isHidden())

    # ---- reordered workflow (user request 2026-07-20) --------------------
    order = [c for c, ph, *_ in WORKFLOW_STEPS if ph == "B"]
    check(f"B phase order {order}",
          order == ["B1", "B2", "B3", "B4", "B5", "B6"])
    check("B3 is Toni, B4 is the LOLO payment",
          checklist.rows["B3"].title.text().endswith("Beritahu Toni (manajer lapangan)")
          and "LOLO" in checklist.rows["B4"].title.text())
    check("C phase reduced to three steps",
          [c for c, ph, *_ in WORKFLOW_STEPS if ph == "C"] == ["C1", "C2", "C3"])

    # Subject is exactly "{exporter}{seq} - " on the steps that were asked for.
    for code, template_id in (("B5", "T08"), ("B5", "T09"), ("B6", "T10"),
                              ("C2", "T11"), ("C3", "T12")):
        draft = builder.build(template_id, row)
        if draft.subject != "AMJ24 - ":
            failures.append(f"{code}/{template_id} subject: {draft.subject}")
    check("email subject is exactly exporter+seq plus a separator",
          not [f for f in failures if "subject:" in f])

    # E4 ticks itself only when files were actually written; a failed export
    # leaves it pending for a retry. Excel is stubbed out.
    from bot_kalung.services.pdf_export import ExportResult

    shipments.set_step(shipment_id, "E4", False)
    window.detail._on_pdf_exported(ExportResult(failed=["SI"]))
    check("a failed export leaves E4 pending",
          not any(s.code == "E4" and s.is_complete
                  for s in shipments.steps(shipment_id)))
    window.detail._on_pdf_exported(
        ExportResult(written=[Path("PDF/SI - AMJ24.pdf")]))
    check("a successful export completes E4",
          any(s.code == "E4" and s.is_complete
              for s in shipments.steps(shipment_id)))

    check("E3 can open Excel directly",
          [a[1] for a in STEP_ACTIONS["E3"]] == ["excel"])
    # The completion-banner block above ticked every phase E step; clear this
    # one so the assertion measures the button, not that leftover state.
    shipments.set_step(shipment_id, "E3", False)
    window.detail._on_action("E3", "excel", "")
    check("opening Excel does not complete E3",
          not any(s.code == "E3" and s.is_complete
                  for s in shipments.steps(shipment_id)))

    window.wizard.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("Checklist OK - all checks passed.")
