# Confirmed deviations from the PRD

Every entry was confirmed with the user on 2026-07-20 after inspecting the live
Google Drive. Where the PRD and the real Drive disagree, the Drive wins.

## 1. Exporters: four, not six (PRD 1.1)

Two changes:

- **TASHA and TTJ are one business** sharing one folder and one sequence series;
  all files on disk use the `TTJ` prefix. Presented once, as **TTJ**.
- **INDO is out of scope entirely.** It has no VGM/SI/Inv/PL workbook — only
  loose PDFs and a standalone `Inv-IBR01.xlsx` — so none of the Excel
  automation applies. Not shown in the app at all.

Supported: **AMJ, TTJ, NIT, THREESTAR**.

## 2. Shipment folders live under a per-exporter subpath (PRD 9.1)

Three layouts exist, driven by `DEFAULT_SHIPMENT_SUBPATH`:

| Exporter | Drive folder | Shipment folders in |
|---|---|---|
| AMJ | `AMJ` | `{year}/` |
| NIT | `NMEHMOOD & CV.Hassan` | `{year}/` |
| TTJ | `TASHA-HUSSAIN-MAJEED` | `{year} Tasha/` |
| THREESTAR | `Three star-waleed` | *(exporter root)* |

`TASHA-HUSSAIN-MAJEED` also contains `2026 MAJEED` and `2026 Hussain` — separate
businesses the app must never scan or modify.

## 3. Folder naming follows the PRD, not the Drive (PRD 9.3)

Existing folders carry buyer/shipper/carrier segments (`23.2x40-karachi-Salim-
KLN-EVE-...`) that appear nowhere in the DO PDF. Rather than add three fields
the worker must type, new folders use the documented format:

```
{seq}.{qty}x{size_short}-{dest_lower}-{booking_no}-{vessel_title} {voyage}-{dd} {mmm_lower}
```

Vessel name is always the full name from the DO, in title case.

## 4. Sequence numbers come from document filenames, not folder prefixes

A folder's numeric prefix is not reliable: `40.1x40-Faizal` contains
`AMJ04-VGM,SI,Inv,PL.xls` — it is really shipment 4. Reading the sequence from
the workbook name also handles NIT's labelled `8.PKBM-3x40-...`. Falls back to
the folder prefix when a folder has no documents.

## 5. Rename patterns are derived from the source folder (PRD 9.2)

The PRD hardcodes `{EXPORTER}{seq}-VGM,SI,Inv,PL.xls`. Real conventions differ
per exporter in extension, embedded destination, code, and zero-padding:

| Exporter | Main workbook | Billing workbook |
|---|---|---|
| AMJ | `AMJ23-VGM,SI,Inv,PL.xls` | `Invoice tagihan AMJ23.xlsx` |
| TTJ | `TTJ04-Karachi-VGM,SI,INV,PL.xlsx` | `Inv-TTJ04.xlsx` |
| NIT | `NIT15-CHENNAI-VGM,SI,INV,PL.xlsx` | `Inv-NIT15.xlsx` |
| THREESTAR | `TSI01-Karachi-VGM,SI,INV,PL.xlsx` | `Inv-TSI01.xlsx` |

New names reuse the source file's own shape with the sequence swapped, so a
convention change on disk is picked up automatically.

The code inside filenames is not always the exporter code: THREESTAR files use
`TSI` (`DEFAULT_FILE_CODES`).

## 6. Only AMJ is `.xls` (build-prompt rule 4)

The other three exporters use `.xlsx`. xlwings handles both, but the "template is
.xls" assumption holds for AMJ alone.

## 7. Subfolders are read from disk, not hardcoded (PRD 9.2 step 2)

The set varies: TTJ adds `Ke Mandiri`, NIT has no `Fumi`, THREESTAR has neither
`Fumi` nor `PEB & NPE`, and THREESTAR spells it `Draft` rather than `Draf`.
Clearing whatever is actually present also satisfies PRD Section 14's
"subfolder missing — log a warning and continue".

## 8. Sheet lookup and cell anchors are fuzzy, not literal (PRD Section 10)

Verified by opening all four workbooks. Details in Section 11 below; the short
version is that sheet names differ per exporter (`'SI '`, `'SI  '`,
`'SI  benar'`), so sheets are matched by normalized prefix, and anchor labels
are matched case-insensitively with the value cell located relative to the
anchor rather than at a fixed column.

## 9. Import permit lives in the shipment folder, not the Drive root (PRD 5)

`G:\My Drive\` contains no `PERMIT-*.pdf` or `IP-*.pdf`. The permit sits inside
each shipment folder (`IP-KHI-E2E018_2026.pdf`) and is carried forward by the
copy plus the keep-patterns rule. The permit check scans the source folder.

## 10. Drive path bootstrapping (PRD 13.0)

The database lives inside the Drive root, but the Drive root is itself a setting
in that database. Broken with a machine-local
`%LOCALAPPDATA%/BotKalung/bootstrap.json` holding only the path.

The folder is named **`zzz JANGAN DISENTUH`** rather than the PRD's plain
`JANGAN DISENTUH` (user request, 2026-07-20): the `zzz ` prefix keeps it at the
bottom of an alphabetical listing, and the folder is additionally marked hidden
on Windows. No migration path exists because nothing has run in production yet.

## 11. Booking number and carrier are read deterministically, not by the LLM

Reported 2026-07-20: on the Evergreen DO the model returned the **application
number** (`26070303078721`) instead of the booking number (`084600048570`).

Cause: the two-column layout makes pdfplumber emit the value *before* its label —

```
084600048570 APPLICATION NO.:26070303078721
BOOKING NO. :
```

`BOOKING NO. :` has nothing after it, while the application number sits right
beside its own label, so it is the more plausible-looking answer.

The booking number is now taken from the document by regex (`services/carrier.py`),
handling both same-line and preceding-line layouts, with the model's answer used
only as a fallback and any disagreement reported to the worker. The extraction
prompt also documents the inverted layout. Carrier (EVERGREEN, OOCL, PIL, …) is
detected from the letterhead the same way and preselects the matching shipping
company in wizard step 2.

This narrows the LLM's role, consistent with PRD Section 8's intent that
everything outside field extraction stays deterministic.

## 12. The LOLO reference table was dropped (PRD 10.2.1 / 10.2.2)

Requirement withdrawn by the user on 2026-07-20. Nothing is written to the SI
sheet, so there is nothing to delete after printing. Step B2 still auto-completes
on a successful print, and the B3/D3 LOLO *payment* steps to Indra are unaffected.

## 13. Communications are narrower than PRD Section 7

All decided with the user on 2026-07-20, after the checklist was built:

**Contacts are not configured.** The contact book exists in the schema but is
not filled in, so no external recipient is resolved. Email drafts pre-fill only
the two teammate addresses (all three worker emails minus "email saya"); the
worker types Nanda, Gucimas, the shipping company and so on into Gmail. As a
consequence, the shipping-company dropdown in wizard step 2 is **optional** —
requiring it would have blocked every shipment.

**Email bodies are empty.** Message content varies too much per shipment to
template. The app supplies the subject line, which is consistent, and the
recipients. `DEFAULT_TEMPLATES` therefore carries subjects only.

**WhatsApp composes nothing.** Those steps are plain checkboxes with a
`Buka WhatsApp` button that brings an existing WhatsApp window to the front, or
opens `web.whatsapp.com` if none is found. Browsers do not expose tab selection
to other programs, and a window's title reflects only its active tab, so
WhatsApp sitting in a background tab cannot be detected — a new tab is opened in
that case. The WhatsApp rows remain in `message_templates` as unused data.

**Only email buttons complete their step.** Opening WhatsApp or the BNCT portal
is not evidence the work happened, so those stay manual. Enforced by action
kind, not by step code, because A3, A4 and E1 carry both kinds of button.

The BNCT portal is `https://portal.bnct-id.com/sso/`.

## 14. Excel support covers all four exporters

Each template was opened and the pipeline run against a copy. Two findings that
the PRD does not mention, both generalised rather than special-cased:

**Merged labels.** NIT merges its SI labels across two columns — `ETD` occupies
`G20:H20`, so its value belongs in `I20`. Writes now skip the label's merge span
(`value_column_after`), which is a no-op where labels are not merged.

**Container type suffix.** AMJ writes `40'HQ`; TTJ, NIT and THREESTAR write
`40'HC`. Taken from whatever the workbook already uses.

**NIT has no linked container rows.** Its SI and P.List describe the cargo in
prose rather than one row per container, so there is nothing to grow or shrink;
the pre-fill reports this and the worker adjusts those sheets by hand. AMJ, TTJ
and THREESTAR all link their rows to the VGM block and adjust automatically.

## 15. Sequence numbers are zero-padded

The document number pads the sequence to two digits — `04072026`, not
`4072026` — matching every live workbook (AMJ23, TTJ04, NIT15, TSI01). Not in
the PRD, and invisible on AMJ, which has been past sequence 10 all year; it
would have produced wrong numbers on TTJ (at 4) and THREESTAR (at 1).

Folder-name padding is taken from the exporter's previous folder instead, since
it differs by exporter: TTJ numbers folders `01.`–`04.`, THREESTAR uses `1.`,
AMJ `23.`.

The month and year always come from the ETD stated on the DO. TSI01 reads
`01072026` because it was prepared in July for an August sailing; the app
writes `01082026` for that shipment and differs from the file on purpose
(user decision, 2026-07-20).

## 16. Workflow reordering and bare email subjects

The B and C phases were reordered on user instruction (2026-07-20) to match the
order the work actually happens in: B1 setup, B2 print SI, B3 tell Toni, B4 pay
LOLO, B5 email the fumigators, B6 email Nanda, then C1 receive container
numbers, C2 email SI+VGM to the carrier, C3 email the PEB. The old C2/C3 emails
moved up into B5/B6 because they are sent before the containers come back.

A3, A4 and B3 send no email at all — they are WhatsApp-or-manual steps, so their
buttons only open WhatsApp and never tick the step.

**Every email subject is exactly `{exporter}{seq}`** — e.g. `AMJ24`. Nothing
else. The worker writes the rest. Bodies stay empty for the same reason
(section 13): content is too shipment-specific to template, but the shipment
identifier makes the mailbox thread and search correctly.

## 17. PDF export (E4) moved into Phase 1

Pulled forward from Phase 2 at the user's request (2026-07-20). Nothing was
missing behind it — the step was greyed out only by its `phase2_only` flag —
so it is now a normal step with an "Ekspor PDF" button.

**Filenames** are `{document} - {exporter}{seq}.pdf`, e.g. `SI - AMJ24.pdf`
(user-specified). The sequence is *not* zero-padded here: this is the shipment
label the team already uses in folder names and email subjects, not the padded
document number from section 15.

**Four files: SI, VGM, Inv BC, PL.** Every live workbook carries two invoice
sheets — `Inv Buyer` and `Inv BC` (Bea Cukai). Only the customs copy (`Inv BC`)
is exported; the buyer's invoice is deliberately left out (user, 2026-07-21).
AMJ's third invoice-like sheet, `PI`, is a proforma and is not exported either.

The export never writes to the workbook: `ExportAsFixedFormat` uses whatever
page setup and print area the sheet already has, so the SI print area is
untouched (a standing rule). E4 ticks itself only when files were written; a
failed export leaves it pending, the same way a failed SI print leaves B2.

## 18. Opening a workbook without leaking an Excel process

`xw.Book(path)` reads as "attach if open, else open", but with no Excel running
it *starts* one and leaves it running with the file locked. Both `printing.py`
and the new export used that pattern, so a print or export from a machine with
no Excel open left a stray process holding the workbook — found by the export
test failing to delete its own temp folder.

`excel.open_book()` now handles the three cases explicitly: reuse a book the
user already has open (leave it exactly as found), open into a running Excel
(close just the book), or start a hidden Excel (quit it).

## 19. Tests must not write the machine-local bootstrap pointer

The Drive path lives in `%LOCALAPPDATA%/BotKalung/bootstrap.json`, which is
machine-global — the one piece of state outside the shared database. Every test
that called `AppContext.create(tmpdir)` overwrote it with a temp folder that was
deleted seconds later, so the next launch found no database and re-ran the setup
wizard. Reported on 2026-07-20 as "it asks for the Drive path and API key again
every time I rebuild"; the trigger was actually running the test suite, which
happens just before each build.

`bootstrap` now honours a `BOTKALUNG_HOME` override, and `tests/sandbox.py`
points it at a temp directory. Every test imports it, and `test_phase1` asserts
the real file is never touched.

## 20. Deleting a shipment sends its folder to the Recycle Bin

Not in the PRD; requested 2026-07-20. The "Hapus Pengiriman" button lives only
on the shipment detail view, where the full shipment and its folder path are in
front of the worker before they confirm — not on the dashboard or history cards,
where a delete is one stray click from the wrong shipment.

The folder goes to the **Recycle Bin**, not a permanent delete, via the shell
`SHFileOperation` with `FOF_ALLOWUNDO` (pywin32, already bundled — no new
dependency). Deleting real export documents should be recoverable. Caveat shown
to the worker in the dialog: Google Drive's virtual G: drive does not always
honour the Recycle Bin and may delete permanently, and there is no reliable way
to detect that beforehand.

Order is folder-first: if the folder cannot be removed (open in Excel or
Explorer), the database record is kept so nothing is left half-deleted. The
record itself is removed with `DELETE FROM shipments`, and the `workflow_steps`
rows cascade (`ON DELETE CASCADE`, with `PRAGMA foreign_keys=ON` per
connection).

The same delete is also offered per row in the History list (added
2026-07-20), so completed shipments — which never appear on the detail view's
active flow — can be cleared without reopening them. Both entry points share
`fileops.delete_shipment_folder` and the same confirmation and folder-first
rule.

## 21. BNCT vessel monitoring (Phase 2, built 2026-07-21)

Brought forward at the user's request. The PRD (Section 15) said "three
unauthenticated endpoints, parse the HTML"; investigating the live portal
(`portal.bnct-id.com/sso/`) turned that into concrete calls. The login page's
own JavaScript polls, unauthenticated:

    POST /sso/monitoring?do=getVesselScheduleDetails&key={ptp|tpkb}
    POST /sso/monitoring?do=getVesselAlongsideDetails&key={ptp|tpkb}

each with an `X-CSRF-TOKEN` header (the `csrfTokenForm` hidden field from the
login page) and that page's session cookie. `services/bnct.py` does the
GET-token-then-POST dance and parses the returned HTML cards with the stdlib
`html.parser` (no bs4/lxml dependency). Fragments captured on 2026-07-21 are
saved under `tests/fixtures/bnct/` so the parser is tested offline against real
data — including one of the team's own vessels, MV. MTT REYA.

A vessel has two phases, and the app records different fields for each:
* **Schedule** — ETD, Open Billing, Open Stacking. A notification fires the
  first time a vessel is seen.
* **Alongside** — the Loading/Discharge/Restow x Plan/Actual/Remain matrix
  (the Total column of each row), plus a done-percentage. Moving to this phase
  notifies; when Loading Remain Total < 5 (PRD threshold) a departure alert
  fires telling the crew to pay LOLO in full to Indra.

Design decisions:
* **In-app only** — a `QTimer` in `BnctController` polls every 5 minutes
  (configurable in Settings) while the app is open; no OS scheduler. The
  network fetch runs on a worker thread, DB work stays on the main thread.
* **Which shipments** — active shipments that carry a vessel name and have not
  ticked step D2 (departed). Polling stops for a shipment once D2 is done.
* **Every check is stored** in `bnct_checks` (cascade-deleted with the
  shipment); notifications fire only on transitions, so a vessel sitting
  alongside does not re-alert every cycle. The detail view shows the latest
  check with a "Periksa Sekarang" button.
* **Matching** requires BOTH a normalized vessel-name match (ignoring "MV."
  etc.) AND a voyage match (>= 3 chars, suffix/substring), to avoid a wrong
  "pay LOLO" alert on a similarly-named ship.

Live network calls are skipped under the offscreen Qt platform, so the test
suite never hits the portal.

## 22. Local CI/CD pipeline (2026-07-21)

`tools/ci.py` is the pipeline; there is no cloud runner because the app needs
Windows, Excel and the team's Google Drive. Stages: lint (pyflakes, honouring
`# noqa` — pyflakes itself ignores it, so the runner post-filters), test (every
`tests/test_*`, classified PASS/SKIP/FAIL by exit code and the "all checks
passed" footer, not by fragile string matching), schema (migration
verification, with an opt-in `--migrate <db>` to update a real database), and
build (PyInstaller + the exe's `--selftest`).

`--fast` skips the Excel/Drive suites and the build (~15s); the git pre-push
hook (`.githooks/pre-push`, enabled per clone with
`git config core.hooksPath .githooks`) runs it so a broken lint/test/schema
blocks a push. The full run, including the exe build, stays manual.

Choosing "schema" as its own stage was the user's call: the app migrates by
`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN` on launch, so the stage
guards against a SCHEMA change the migration path does not actually apply.

## 23. Migrate on every open, not only at setup (2026-07-21)

Bug: `AppContext.create()` ran `db.initialize()` (the migration), but
`attach()`/`load()` did not. So an install configured by an older build never
received new tables or columns — the first query against the new schema failed
with "no such table: bnct_checks" (reported after the BNCT feature shipped).
The same latent gap would have hit any earlier column add too.

Fix: `attach()` now calls `initialize()` on every open. It is idempotent
(`CREATE TABLE IF NOT EXISTS` + additive `ALTER`), so re-running it each launch
is safe and brings any existing shared database up to the current schema. The
regression is covered through the real `AppContext.load()` path in
`test_migration`, not just `Database.initialize()` directly — testing the
lower level is exactly why the bug shipped unnoticed.

## 24. In-app notification centre (2026-07-21)

Windows ignores the duration an app requests for a tray toast (it uses the
system timeout, ~5s, then the Action Center), so `QSystemTrayIcon.showMessage`'s
15s hint does nothing. To make notifications persist, each BNCT transition is
also stored in a `notifications` table and surfaced in-app: a "Notifikasi"
sidebar item with an unread counter, and a list where clicking a notification
marks it read and opens its shipment. The tray toast still fires for the live
nudge; the centre is the durable record.

Notifications are written inside `BnctMonitor.process()` alongside the check,
so only the app instance that actually detects a transition creates the row
(no cross-instance duplicates). They live in the shared database, so the log
and read/unread state are team-wide — reasonable for a team coordinating the
same shipments; per-user state would be the change point if ever wanted. Rows
cascade with their shipment on delete.

## Still open

- ~~BNCT portal monitoring with the result recorded in the app~~ — closed
  2026-07-21 (section 21): the login page's unauthenticated endpoints are
  scraped, results recorded, notifications on transitions.
- ~~Excel sheet layouts for the other exporters are unverified~~ — closed
  2026-07-20: all four templates opened and covered by tests (section 14).
- ~~Message template bodies T01–T16 (PRD Open Item 1)~~ — closed 2026-07-20:
  bodies are intentionally empty (section 13 above).
- Exporter full company names for `{exporter_full}` (PRD Open Item 2). Only
  needed if a subject line references the formal company name; none do today.
- NIT's SI and P.List container counts are adjusted by hand (section 14).
