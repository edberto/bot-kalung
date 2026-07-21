# Bot Kalung — Product Requirements Document
**Broomstick Division — Windows Desktop App**  
*Version 0.3 · Draft*

---

## Document Purpose

This document is the authoritative specification for an AI coding agent tasked with building the Export Document Manager. It defines every screen, layout, component, behavior, data model, and error state required to produce a working application. The target is a Python-based Windows desktop application using PyQt6.

The app helps a small team of document coordinators manage the full export workflow for broomstick shipments on behalf of multiple exporter companies. It automates folder setup, document pre-fill, communication drafting, and shipment progress tracking.

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Language | Python 3.11+ | Application logic |
| UI | PyQt6 | Desktop UI framework |
| Excel | xlwings | Read/write Excel files via installed Microsoft Excel |
| PDF | pdfplumber | Extract text from DO PDF files |
| LLM | Anthropic Python SDK (Claude Haiku) or Ollama (local) | DO field extraction only |
| Storage | SQLite via sqlite3 | Shipment records, settings, step completion state |
| File ops | pathlib + shutil | Folder copy, rename, delete operations on Google Drive sync |
| Gmail | webbrowser + Gmail URL scheme | Open Gmail compose with pre-filled To/Subject/Body |
| WhatsApp | webbrowser (whatsapp:// then fallback to web.whatsapp.com) | Open WhatsApp with pre-filled message |
| Packaging | PyInstaller | Build single .exe for distribution |
| Config | SQLite (same DB as shipment data) | App settings, contact book, message templates |

---

# 1. Product Overview

## 1.1 Background & Problem

The company exports broomsticks on behalf of six exporter companies: INDO, AMJ, TTJ, TASHA, NIT, and THREESTAR. Each shipment involves coordinating documents and communications across multiple parties: the exporter, a field manager (Toni), a shipping company, fumigators (Gucimas or Pestcindo), a third-party document handler (Nanda), and a port terminal (BNCT Belawan).

Currently all work is done manually: copying Google Drive folders, renaming files, entering repetitive data into Excel sheets, and composing the same emails and WhatsApp messages from scratch each time. This is slow, error-prone, and hard to track across multiple simultaneous shipments.

## 1.2 Goals

- Automate new shipment folder setup from a DO PDF in under 60 seconds
- Pre-fill Excel documents (VGM, SI, Packing List) with shipment data extracted from the DO
- Provide one-click communication buttons that open pre-filled Gmail or WhatsApp drafts
- Track workflow progress across multiple simultaneous active shipments
- Maintain a searchable history of past completed shipments

## 1.3 Non-Goals (Phase 1)

- Automated BNCT vessel monitoring (Phase 2)
- Container/seal number entry into Excel (Phase 2)
- PDF export of Excel sheets (Phase 2)
- Print management (Phase 2)
- Automated email inbox scanning (Phase 3)
- Support for Turpentine/Gum Rosin exporters HAI and JMI (separate scope)
- Multi-user sync or cloud backup — shared via Google Drive SQLite (see Section 13.0)

## 1.4 Language

The entire app UI must be in Bahasa Indonesia. All labels, button text, placeholder text, error messages, toast notifications, dialog text, and tooltip text must be written in Indonesian. English is permitted only in: code identifiers (database column names, template variable placeholders such as `{booking_no}`), technical file paths, and content that the user explicitly types or imports (e.g. DO field values, contact names).

---

# 2. Application Architecture

## 2.1 Window Structure

The app is a single resizable window (minimum 1100 × 700 px). It is divided into two persistent panels:

- Left sidebar (250 px fixed width): navigation, active shipments list
- Main content area (remaining width): context-dependent view

The sidebar is always visible. The main content area renders one of four views depending on the current navigation state: Dashboard, New Shipment Wizard, Shipment Detail, or Settings.

## 2.2 Screen Inventory

| Screen | Route / Trigger | Description |
|---|---|---|
| Dashboard | App launch / sidebar Home | Overview of active shipments and quick-start actions |
| New Shipment Wizard | "New Shipment" button | Step-by-step flow to create a shipment folder and pre-fill documents |
| Shipment Detail | Click a shipment in sidebar or dashboard | Full workflow checklist for one shipment with action buttons |
| Shipment History | Sidebar "History" link | Searchable list of completed shipments with metadata |
| Settings | Sidebar "Settings" gear icon | Google Drive path, LLM config, contacts, message templates |
| First-Launch Setup | Auto-shown on first run | Wizard to configure Drive path, LLM, and key contacts before first use |

## 2.3 Navigation Flow

Navigation state is managed in a single controller. The sidebar reflects the active view. Navigating away from an in-progress wizard prompts a confirmation dialog ("Discard unsaved shipment setup?").

- App launch → if first run: First-Launch Setup → Dashboard; else: Dashboard
- Dashboard → click "New Shipment" → New Shipment Wizard
- Dashboard → click shipment card → Shipment Detail
- Sidebar shipment entry → Shipment Detail
- Wizard Step 3 success → Shipment Detail (newly created shipment)
- Sidebar "History" → Shipment History
- Sidebar Settings gear → Settings
- Settings "Back" or sidebar navigation → returns to previous view

## 2.4 Sidebar Layout

Top to bottom:

1. App logo / name ("Bot Kalung" or similar) — top left
2. "+ New Shipment" button — prominent, below logo
3. Section label: ACTIVE SHIPMENTS
4. List of active shipments (scrollable if > 5). Each entry shows:
   1. Exporter badge (colored chip: e.g. AMJ in blue)
   2. Vessel name + voyage (truncated)
   3. ETD date
   4. Progress indicator: "X / Y steps done" or a mini progress bar
5. Section label: NAVIGATION
6. "History" link (clock icon)
7. "Settings" link (gear icon)

Clicking an active shipment entry loads its Shipment Detail view. The currently active view is highlighted in the sidebar.

> **Note:** Exporter badge colors: AMJ = blue, INDO = teal, TTJ = purple, TASHA = orange, NIT = green, THREESTAR = red. Colors are consistent throughout the app wherever the exporter is referenced.

---

# 3. First-Launch Setup

Shown automatically the first time the app runs (detected by absence of a config record in SQLite). Displayed as a full-screen overlay with a 3-step wizard. Cannot be dismissed without completing all required fields.

## Step 1 of 3: Google Drive Path

- Instruction text: "Select the root folder of your Google Drive sync on this computer."
- Folder picker button → opens Windows folder browser dialog
- Selected path is displayed in a read-only text field
- Validation: the selected folder must contain at least one of the exporter subfolders (INDO, AMJ, TTJ, TASHA, NIT, THREESTAR). If none found, show inline error: "Could not find any exporter folders at this location. Please select the correct Google Drive root."
- "Next" button is disabled until validation passes

## Step 2 of 3: LLM Provider

- Two radio options:
  - Claude API (Anthropic) — recommended. Input field for API key. Link: "Get an API key at console.anthropic.com". Note: "Approximately $0.001 per shipment. Separate from Claude.ai subscription."
  - Local LLM (Ollama) — free, runs offline. Input field for Ollama model name (default: llama3). Note: "Ollama must be installed and running on this machine."
- "Test Connection" button — sends a short test prompt and displays "✓ Connection successful" or the error message
- "Next" is disabled until test passes or user explicitly clicks "Skip for now" (small secondary link)

## Step 3 of 3: Identitas & Kontak

Starts with an identity selector, followed by the contact form. All contact fields are optional at setup (can be completed later in Settings).

### Email saya

A dropdown with the 3 fixed worker emails: `ongkalung@gmail.com`, `achentan96@gmail.com`, `edbertongko88@gmail.com`. Worker selects which one is theirs. Required before proceeding — "Selesai" button is disabled until a selection is made. If the database already has contacts configured (i.e. another worker set up the app first on a different machine), a note is shown: "Kontak sudah dikonfigurasi. Pilih email Anda untuk melanjutkan." — only the email selector is shown, the contact form is hidden.

### Kontak

A form with the most commonly used contacts. All fields are optional at setup (can be completed later in Settings). Fields:

| Contact | Field(s) | Used In |
|---|---|---|
| Nanda | Email address | Email templates |
| Gucimas (fumigator) | Email address | Email templates |
| Pestcindo (fumigator) | Email address | Email templates |
| Field manager Toni | WhatsApp number (with country code) | WhatsApp templates |
| Indra (LOLO payments) | WhatsApp number | Reminder steps |
| Default shipping company | Name + Email address | Email templates (more companies can be added in Settings) |

- "Finish Setup" button saves all config and navigates to Dashboard

---

# 4. Dashboard

The default view after launch (when setup is complete). Shown in the main content area when no shipment is selected.

## 4.1 Layout

1. Header bar: "Dashboard" title + date
2. Summary row (3 stat cards side by side):
   1. Active Shipments — count
   2. Steps Overdue — count of steps where ETD has passed and step is incomplete
   3. Completed This Month — count
3. Active Shipments section: grid of shipment cards (2 columns). Each card shows:
   1. Exporter badge (colored chip)
   2. Vessel name + voyage
   3. ETD date (red if ≤ 3 days away)
   4. Destination
   5. Booking number
   6. Progress bar + "X of Y steps complete"
   7. "Open" button → navigates to Shipment Detail
4. Empty state (no active shipments): centered illustration + text "No active shipments. Start by clicking + New Shipment."
5. "+ New Shipment" button — large, prominent, below the grid

## 4.2 Empty State

When no active shipments exist, the shipment grid area is replaced by a centered empty state: a simple icon, the text "No active shipments", and a large "+ New Shipment" button.

---

# 5. New Shipment Wizard

A 3-step flow shown in the main content area. A step indicator at the top shows "Step 1 of 3", "Step 2 of 3", "Step 3 of 3". A "Cancel" button in the top-right returns to Dashboard with a confirmation dialog.

## Step 1: Exporter & DO Upload

### Exporter Selection

A row of six clickable cards, one per exporter (INDO, AMJ, TTJ, TASHA, NIT, THREESTAR). Each card shows the exporter code with its assigned color. Only one can be selected at a time. Selected card has a highlighted border.

### DO Upload

A drag-and-drop zone below the exporter cards. Accepts PDF files only. The user can also click the zone to open a file picker. Once a file is selected, the filename is displayed with a remove (×) button.

### Validation & Proceed

- "Next" button is disabled until an exporter is selected AND a PDF file is loaded
- Clicking "Next" triggers LLM extraction (see Section 7.1). A spinner replaces the "Next" button during extraction. On failure, an error banner is shown: "Could not extract DO fields. You can fill them in manually." and the wizard advances to Step 2 with empty fields.

## Step 2: Review Extracted Fields

A form displaying all extracted fields, pre-populated from the LLM output. All fields are editable. The user must confirm before proceeding.

| Field | UI Control | Pre-populated From | Notes |
|---|---|---|---|
| Booking Number | Text input | LLM extraction | Required |
| Vessel Name | Text input | LLM extraction | Required |
| Voyage | Text input | LLM extraction | Required |
| ETD at Belawan | Date picker | LLM extraction | Required |
| Destination Port | Text input | LLM extraction | Required |
| Destination Country | Text input | LLM extraction | Required |
| Container Quantity | Number spinner (min 1) | LLM extraction | Required |
| Container Size (short) | Text input (e.g. 40') | Derived from LLM raw size | Required |
| Empty Pickup Location | Text input | LLM extraction | Optional — used in Toni WA template |
| Sequence Number | Number spinner | Auto-detected from Drive folder | Shows detected value; user can override. Displays note: "Detected from last [EXPORTER] folder: [n]" |
| Quarantine Required | Read-only flag (Yes/No) | Derived from destination country | Yellow warning badge if Yes |
| Shipping Company | Dropdown (from contacts list) | None — user selects | Required. Lists all configured shipping companies by name. Shipping company can vary per shipment. |

> **Note:** If destination country is in the quarantine-required list (currently: Pakistan), show a yellow info banner: "⚠ Negara tujuan ini memerlukan pemeriksaan karantina. Ingat untuk menulis tanggal dan lokasi di SI yang dicetak."

### IP Permit Check

After the destination country is confirmed (either from LLM extraction or user edit), the app performs a background IP permit check:

- Scan `{google_drive_root}` for any file matching `PERMIT-*.pdf` or `IP-*.pdf` (case-insensitive). Search only the Drive root, not subfolders.
- If no matching file is found: show orange warning banner: "⚠ Tidak ditemukan file izin impor (PERMIT-*.pdf) di Google Drive. Pastikan izin tersedia sebelum pengiriman."
- If a file is found: send the file text (extracted with pdfplumber) to the LLM with the prompt below. Extract the expiry date from the response.
- If expiry date is in the past: show red warning banner: "⚠ Izin impor sudah kedaluwarsa pada [tanggal]. Perbarui dokumen PERMIT sebelum melanjutkan."
- If expiry date is within 30 days: show yellow warning banner: "⚠ Izin impor akan kedaluwarsa pada [tanggal] — segera perbarui."
- If expiry date is more than 30 days away: show green info: "✓ Izin impor berlaku hingga [tanggal]."
- If LLM cannot extract expiry date: show orange warning: "⚠ Tidak dapat membaca tanggal kedaluwarsa dari file izin. Periksa dokumen secara manual."

LLM prompt for IP permit expiry extraction:

```
Extract the expiry date from this import permit document.
Return a JSON object: { "expiry_date": "YYYY-MM-DD" }
If you cannot find an expiry date, return: { "expiry_date": null }
Return only valid JSON. No explanation.
--- DOCUMENT TEXT BELOW ---
{permit_text}
```

> **Note:** All warning banners on this screen are advisory only — they do not block the user from proceeding to Step 3. The permit check runs in the background (non-blocking) while the user reviews fields; the banner appears when the check completes. New file naming convention going forward: `PERMIT-[description]-[YYYY]-[MM].pdf`, e.g. `PERMIT-Pakistan-2026-01.pdf`

- "Back" returns to Step 1 (file and exporter selection preserved)
- "Next" is disabled until all required fields are filled
- "Next" → proceeds to Step 3 (no file operations yet)

## Step 3: Confirm & Create

A summary card showing all confirmed details. A "What will happen" section lists the operations the app is about to perform:

- Copy folder "[last folder name]" → "[new folder name]"
- Clear 6 subfolders (Dok kirim, Draf, Foto, Fumi, PDF, PEB & NPE)
- Rename Excel files to `AMJ[n]-VGM,SI,Inv,PL` and `Invoice tagihan AMJ[n]`
- Copy DO PDF into new folder root
- Pre-fill Excel: VGM date, number, booking no., vessel, container rows; SI number, container rows, ETD; P.List rows

"Create Shipment" button — triggers all file operations. While running, the button is replaced by a progress indicator with status text ("Copying folder...", "Clearing subfolders...", "Renaming files...", "Updating Excel..."). Each completed operation is shown with a green checkmark as it finishes.

On success: the app immediately triggers auto-print of the SI sheet (see below), then shows "✓ Pengiriman berhasil dibuat" message. Two buttons appear:

- "Buka Pengiriman" → navigates to Shipment Detail for the new shipment
- "Buka Folder" → opens the new Google Drive folder in Windows Explorer

### Auto-Print SI Sheet

Immediately after Excel pre-fill completes successfully, the app prints the SI sheet automatically:

- Call `ws.api.PrintOut()` via xlwings on the SI sheet. This uses Excel's built-in print settings (page setup, margins, print area) as already configured in the template — the app does not change any print settings.
- Before printing, show a non-blocking toast: "Mencetak SI..." with a spinner. On completion: "✓ SI dicetak."
- If printing fails (no printer configured, printer offline, etc.): show orange warning toast: "⚠ Gagal mencetak SI secara otomatis. Cetak manual dari Excel." Do not block the success flow.

> **Note:** After a successful print: (1) delete the LOLO reference table from the SI sheet (Section 10.2.2), (2) auto-mark step B2 complete. If the print fails, do neither — B2 stays pending and the Shipment Detail screen shows a "Cetak ulang SI" retry button for step B2.

On failure at any step: a red error banner shows the specific failure ("Failed to copy folder: [reason]"). Operations already completed are rolled back where possible (the copied folder is deleted if the operation fails mid-way). The "Create Shipment" button re-enables for retry.

---

# 6. Shipment Detail

The primary working screen. Loaded when a shipment is selected from the sidebar or dashboard.

## 6.1 Header Bar

A persistent header at the top of the main content area (below the app title bar) showing:

- Exporter badge (colored chip)
- Vessel name + voyage
- ETD date (red if ≤ 3 days away)
- Booking number (muted gray)
- Destination (muted gray)
- "Open Folder" button (folder icon) — opens the shipment folder in Windows Explorer
- "Open Excel" button — opens the VGM/SI/Inv/PL Excel file via the default app
- Completion badge: "X / Y steps complete" with a small progress bar

## 6.1.1 Quarantine Reminder Banner

If `quarantine_required` is true for this shipment, show a persistent yellow banner directly below the header bar (above the workflow checklist): "⚠ Karantina diperlukan — tulis tanggal dan lokasi pemeriksaan di SI yang sudah dicetak." The banner remains visible until step B3 (Pay LOLO empty) is marked complete, at which point it is dismissed.

## 6.2 Workflow Checklist

Below the header, the main area contains a scrollable vertical checklist of workflow steps. Steps are grouped into 5 phases. Each phase has a section header with a collapse/expand toggle.

Each step is rendered as a row with the following layout (left to right):

- Status icon: ○ (pending), ◉ (in progress), ✓ (complete), — (N/A or skipped)
- Step number + title
- Step description (1 line, gray, smaller font)
- Action button(s) — right-aligned. Absent for purely manual steps.
- Manual complete checkbox — far right. Always present on every step, including auto-completed ones. Clicking it toggles the step between complete and incomplete.
- Unchecking a step: always silent — just reverts the step to pending with no side effects.
- Re-checking a step that has automatic side effects: show a confirmation dialog before executing the side effects again. Steps with no side effects toggle silently. Confirmation dialogs per step:
  - B2 (Cetak SI): "Cetak ulang lembar SI dan hapus tabel LOLO dari Excel?" → [Ya, Cetak Ulang] [Batal]
  - Any future steps with auto-actions follow the same pattern: describe the action in the dialog so the worker knows what will happen.
  - Re-checking a step that was originally auto-completed but has no re-runnable action (e.g. B1 Setup complete): toggle silently, no dialog.

> **Note:** Action buttons auto-mark the step as complete when clicked. The manual checkbox can override this in either direction.

Steps that are complete are shown with a green left border, muted text, and a checked icon. Steps where the ETD has passed and the step is still incomplete are shown with an orange left border ("overdue").

## 6.3 Workflow Steps Definition

Below is the complete step definition for Phase 1. Steps marked "Phase 2" are visible but disabled (grayed out with a "Coming soon" badge) to show the full workflow without confusing the user.

### Phase A: Pre-Shipment Coordination

| # | Title | Description | Action Button(s) | Auto-complete? |
|---|---|---|---|---|
| A1 | Receive DO | DO PDF received and shipment created | — | Yes — auto-complete on shipment creation |
| A2 | Ask exporter: buyer & storage | WhatsApp the exporter head to ask for buyer name, storage location, and any buyer-specific documents | WhatsApp [Exporter Head] | Yes — on button click |
| A3 | Ask shipper: ship schedule | Request ship schedule confirmation via email and/or WhatsApp | Email Shipper \| WhatsApp Shipper | Yes — on first button click |
| A4 | Ask shipping company: empty container | Request the empty container fetch schedule | Email [Shipping Co.] \| WhatsApp [Shipping Co.] | Yes — on first button click |

### Phase B: Document Preparation

| # | Title | Description | Action Button(s) | Auto-complete? |
|---|---|---|---|---|
| B1 | Setup complete | Folder created and Excel pre-filled | — | Yes — auto-complete on wizard finish |
| B2 | Cetak SI | SI sheet is printed automatically after Excel pre-fill completes (see Section 5, Auto-Print SI Sheet). The LOLO reference table is deleted from the Excel file immediately after printing (see Section 10.2.2). If quarantine is required, a reminder banner is shown on the Shipment Detail screen. | — | Yes — auto-complete immediately after auto-print succeeds. If auto-print fails, step stays pending and worker can retry from Shipment Detail. |
| B3 | Pay LOLO empty to Indra | Make LOLO empty payment | WhatsApp Indra | No — manual checkbox |
| B4 | Notify Toni (field manager) | Send WhatsApp to Toni with: empty container fetch date & location, goods pickup date & storage address, stuffing schedule. Attach DO manually. | WhatsApp Toni | Yes — on button click |

### Phase C: Document Submission

| # | Title | Description | Action Button(s) | Auto-complete? |
|---|---|---|---|---|
| C1 | Receive container & seal numbers | Wait for Toni to send container and seal numbers. Enter them in the Excel file manually. [Phase 2: entry field in app] | — | No — manual checkbox |
| C2 | Email fumigator | Send SI and IP Permit doc to fumigator. Choose Gucimas or Pestcindo. | Email Gucimas \| Email Pestcindo | Yes — on button click |
| C3 | Email Nanda: SI, VGM, Invoice, PL | Send documents to Nanda for PEB and COO preparation | Email Nanda | Yes — on button click |
| C4 | Email shipping company: SI + VGM | Send SI and VGM to shipping company | Email [Shipping Co.] | Yes — on button click |
| C5 | Email PEB to shipping company | After receiving PEB from Nanda, forward to shipping company | Email [Shipping Co.] | Yes — on button click |

### Phase D: Vessel Monitoring

| # | Title | Description | Action Button(s) | Auto-complete? |
|---|---|---|---|---|
| D1 | Wait for fumigation certificate | Fumigator sends fumigation certificate | — | No — manual checkbox |
| D2 | Monitor BNCT: vessel alongside | Check BNCT portal for "Vessel Alongside" status. When Loading Remain Total < 5, vessel is departing. | Open BNCT Portal [Phase 2: auto-monitor] | No — manual checkbox |
| D3 | Pay LOLO full to Indra | Make LOLO full payment once vessel is alongside | WhatsApp Indra | No — manual checkbox |

### Phase E: Finalization

| # | Title | Description | Action Button(s) | Auto-complete? |
|---|---|---|---|---|
| E1 | Request BL draft | Ask shipping company for Bill of Lading draft | Email [Shipping Co.] \| WhatsApp [Shipping Co.] | Yes — on first button click |
| E2 | Revise & confirm BL | Review BL draft, request revisions if needed, confirm final | — | No — manual checkbox |
| E3 | Update Excel with confirmed date & ETD | Update date and ETD in all sheets based on confirmed BL [Phase 2: in-app entry] | — | No — manual checkbox |
| E4 | Export to PDF | Export SI, VGM, Invoice, PL sheets to PDF in the PDF subfolder [Phase 2] | [Phase 2] | Phase 2 |
| E5 | Print documents | Print Invoice & PL (4 copies each), Phyto (1), COO (1), Fumigation cert (1) [Phase 2] | — | No — manual checkbox |
| E6 | Notify exporter head | Send final notification to exporter head that shipment is complete | WhatsApp [Exporter Head] | Yes — on button click |

## 6.4 Marking a Shipment Complete

When all steps in Phase E are marked complete, the app shows a "Mark Shipment Complete" banner at the bottom of the checklist. Clicking it:

1. Sets the shipment status to "Completed" in SQLite
2. Records the completion timestamp
3. Moves the shipment from "Active Shipments" in the sidebar to "History"
4. Shows a success toast: "Shipment [AMJ23] marked complete."
5. Navigates to Dashboard

---

# 7. Communication Templates

## 7.1 How Action Buttons Work

When the user clicks an action button in the workflow (e.g. "Email Nanda" or "WhatsApp Toni"), the app:

1. Resolves the message template for that action (from the template store in SQLite)
2. Substitutes all `{placeholder}` variables with values from the active shipment record
3. Opens the appropriate channel:
   1. Email → browser opens Gmail compose URL with To, Subject, and Body pre-filled (URL-encoded). The To field is built as: the configured external contact for that step + the other 2 worker emails (all 3 worker emails minus "email saya"). Format: `https://mail.google.com/mail/u/0/?view=cm&to={to}&su={subject}&body={body}` where `{to}` is comma-separated.
   2. WhatsApp → app tries `whatsapp://send?phone={number}&text={message}`; if that fails (WhatsApp Desktop not installed), falls back to `https://web.whatsapp.com/send?phone={number}&text={message}` in the default browser
4. Marks the step as complete (unless the step definition says "No — manual checkbox")

## 7.2 Available Template Variables

| Variable | Value |
|---|---|
| `{exporter}` | Exporter code, e.g. AMJ |
| `{exporter_full}` | Full exporter company name (configured in Settings) |
| `{seq}` | Shipment sequence number, e.g. 23 |
| `{booking_no}` | DO booking number |
| `{vessel}` | Vessel name, e.g. EVER CONCERT |
| `{voyage}` | Voyage code, e.g. 0800-088N |
| `{vessel_voyage}` | Combined vessel + voyage, e.g. EVER CONCERT 0800-088N |
| `{etd}` | ETD formatted as "03 August 2026" |
| `{etd_short}` | ETD formatted as "03 Aug 2026" |
| `{destination}` | Destination port, e.g. KARACHI |
| `{destination_country}` | Destination country, e.g. PAKISTAN |
| `{container_qty}` | Number of containers, e.g. 2 |
| `{container_size}` | Container size, e.g. 40' |
| `{empty_pickup}` | Empty container pickup location |
| `{folder_name}` | Full shipment folder name |

## 7.3 Template Definitions

Default template bodies are placeholders — to be replaced with actual message content once example messages are provided by the user. The structure below defines which template exists, its channel, recipient, and subject (where applicable). Body text is TBD.

| ID | Step | Channel | Recipient Source | Subject (Email only) |
|---|---|---|---|---|
| T01 | A2 — Ask exporter | WhatsApp | Exporter head contact for `{exporter}` | — |
| T02 | A3 — Ask shipper (email) | Email | Configured: shipper email | Ship Schedule – Booking `{booking_no}` – `{vessel_voyage}` |
| T03 | A3 — Ask shipper (WA) | WhatsApp | Configured: shipper WA | — |
| T04 | A4 — Ask shipping co (email) | Email | Configured: shipping company email | Empty Container Schedule – Booking `{booking_no}` |
| T05 | A4 — Ask shipping co (WA) | WhatsApp | Configured: shipping company WA | — |
| T06 | B3 — WhatsApp Indra (LOLO empty) | WhatsApp | Configured: Indra WA | — |
| T07 | B4 — Notify Toni | WhatsApp | Configured: Toni WA | — |
| T08 | C2 — Email Gucimas | Email | Configured: Gucimas email | Fumigation Request – `{exporter}` `{seq}` – `{vessel_voyage}` |
| T09 | C2 — Email Pestcindo | Email | Configured: Pestcindo email | Fumigation Request – `{exporter}` `{seq}` – `{vessel_voyage}` |
| T10 | C3 — Email Nanda | Email | Configured: Nanda email | Shipping Documents – `{exporter}` `{seq}` – `{vessel_voyage}` |
| T11 | C4 — Email shipping co: SI+VGM | Email | Configured: shipping company email | SI & VGM – Booking `{booking_no}` – `{vessel_voyage}` |
| T12 | C5 — Email PEB | Email | Configured: shipping company email | PEB Submission – Booking `{booking_no}` |
| T13 | D3 — WhatsApp Indra (LOLO full) | WhatsApp | Configured: Indra WA | — |
| T14 | E1 — Request BL (email) | Email | Configured: shipping company email | BL Draft Request – Booking `{booking_no}` |
| T15 | E1 — Request BL (WA) | WhatsApp | Configured: shipping company WA | — |
| T16 | E6 — Notify exporter head | WhatsApp | Exporter head contact for `{exporter}` | — |

---

# 8. DO Parsing (LLM)

The LLM is used exclusively for field extraction from the DO PDF. All other app logic is deterministic.

## 8.1 Extraction Flow

1. App extracts raw text from the DO PDF using pdfplumber
2. Raw text is sent to the configured LLM with the prompt below
3. LLM returns a JSON object with the extracted fields
4. App validates the JSON (all required keys present, ETD is a valid date, container quantity is a positive integer)
5. Validated values are pre-populated into the Step 2 review form
6. If LLM call fails or JSON is invalid, Step 2 form opens with all fields empty and an error banner

## 8.2 LLM Extraction Prompt

The following prompt is sent to the LLM with the DO text appended:

```
You are extracting fields from a shipping Booking Confirmation (Delivery Order) document.
Extract the following fields and return them as a JSON object with exactly these keys:
{
  "booking_number": "string — the booking or DO number",
  "vessel_name": "string — vessel name only, no voyage code",
  "voyage": "string — voyage code only",
  "etd_belawan": "YYYY-MM-DD — ETD at the port of loading (Belawan)",
  "destination_port": "string — port of discharging, city name only",
  "destination_country": "string — country of port of discharging",
  "container_quantity": integer,
  "container_size_raw": "string — full container type as written, e.g. 40' HI-CUBE",
  "empty_pickup_location": "string — empty container pickup location, or null if not found"
}
Return only valid JSON. No explanation. If a field cannot be found, use null.
--- DOCUMENT TEXT BELOW ---
{raw_text}
```

## 8.3 Post-Processing

After LLM extraction, the app applies these deterministic transformations programmatically:

- `container_size_short`: truncate `container_size_raw` at the first character after the foot symbol ('). E.g. `"40' HI-CUBE"` → `"40'"`
- `quarantine_required`: check if `destination_country` is in the quarantine list (currently hardcoded: `["PAKISTAN"]`). Returns boolean.
- `vgm_date_month`: if `etd_belawan` day is within the last 3 calendar days of its month, use the following month; otherwise use the same month. Format as `"MMMM YYYY"` in uppercase (e.g. `"AUGUST 2026"`).
- `si_number` / `vgm_number`: constructed as `[seq][MM][YYYY]` where MM and YYYY come from `etd_belawan` (not from `vgm_date_month`).

---

# 9. Folder & File Operations

## 9.1 Source Folder Detection

When creating a new shipment for a given exporter (e.g. AMJ), the app scans the exporter's subfolder at `{google_drive_root}/{exporter}/` for folders whose names start with a numeric prefix followed by a period (e.g. `"22."`). It sorts these numerically by prefix and selects the highest as the source to copy. The next sequence number is highest + 1.

> **Note:** Folders that do not match the pattern `^[0-9]+\.` are ignored (e.g. the "1-30" folder seen in the AMJ root).

## 9.2 Folder Operations Sequence

All operations are performed in this exact order. If any step fails, the app rolls back by deleting the partially-created destination folder (if it was created) and reports the specific error.

| Order | Operation | Detail |
|---|---|---|
| 1 | Copy source folder | `shutil.copytree(source_folder, destination_folder)`. Destination must not already exist. |
| 2 | Clear subfolders | For each of [Dok kirim, Draf, Foto, Fumi, PDF, PEB & NPE]: delete all files and subfolders inside, but keep the folder itself. |
| 3 | Delete loose files | Delete all files in the root of the destination folder EXCEPT those matching the three keep patterns below. |
| 4 | Copy DO PDF | Copy the original DO PDF into the destination folder root. Preserve the original filename. |
| 5 | Rename Excel (main) | Find the file matching `*VGM*SI*Inv*PL*.xls` (case-insensitive glob). Rename to `{EXPORTER}{seq}-VGM,SI,Inv,PL.xls` |
| 6 | Rename Invoice tagihan | Find the file matching `*Invoice*tagihan*.xlsx` (case-insensitive glob). Rename to `Invoice tagihan {EXPORTER}{seq}.xlsx` |
| 7 | Rename folder | Rename the destination folder to the final name per the naming convention. |

### Files to Keep (step 3)

- Any file matching `*VGM*SI*Inv*PL*.xls` — the main Excel file
- Any file matching `*Invoice*tagihan*.xlsx` — the billing Excel file
- Any file matching `PERMIT-*.pdf` — the import permit PDF (new standardized naming convention going forward)
- Any file matching `IP-*.pdf` — legacy import permit naming (kept for backward compatibility)

> **Note:** All other files in the root (e.g. old DO PDFs, stray documents) are deleted.

## 9.3 Folder Naming Convention

```
{seq}.{qty}x{size_short}-{dest_lower}-{booking_no}-{vessel_title} {voyage}-{dd} {mmm_lower}
```

| Token | Value | Example |
|---|---|---|
| `{seq}` | Sequence number (integer, no padding) | 23 |
| `{qty}` | Container quantity | 2 |
| `{size_short}` | Container size short (strip everything after the foot mark: 40' HI-CUBE → 40') | 40' |
| `{dest_lower}` | Destination port, lowercased | karachi |
| `{booking_no}` | Booking number from DO | 084600048570 |
| `{vessel_title}` | Vessel name in title case | Ever Concert |
| `{voyage}` | Voyage code as-is | 0800-088N |
| `{dd}` | ETD day, zero-padded | 03 |
| `{mmm_lower}` | ETD month abbreviation, lowercase | aug |

Full example: `23.2x40'-karachi-084600048570-Ever Concert 0800-088N-03 aug`

---

# 10. Excel Pre-fill

After all file operations complete, the app opens the main Excel file (`{EXPORTER}{seq}-VGM,SI,Inv,PL.xls`) using xlwings with the Excel application visible (`app.visible = True`). It makes the following changes, saves, and leaves the file open for user review.

> **Note:** xlwings requires Microsoft Excel to be installed. If Excel is not found, show error: "Microsoft Excel is required for document pre-fill. Please install Excel and try again."

> **Note:** If the file is already open in Excel when the app tries to open it, xlwings will connect to the existing instance. The app should handle this gracefully.

## 10.1 VGM Sheet

| Field | How to Locate | Value to Set | Formatting |
|---|---|---|---|
| DATE cell | Find cell containing text "DATE" in column B; set the adjacent cell in column E | `vgm_date_month` (e.g. "AUGUST 2026") | Font color: Red (RGB 255,0,0) |
| NO cell (VGM number) | Find cell containing "NO :" or "VGM-" in column B | `"VGM-{seq}{MM}{YYYY}"` where MM/YYYY from `etd_belawan` | Default formatting |
| BOOKING NO / DO NO | Find cell containing "BOOKING NO" in column B; set adjacent cell E | `booking_number` (string) | Default formatting |
| VESSEL NAME | Find cell containing "VESSEL NAME" in column B; set adjacent cell E | `"{vessel_name} {voyage}"` in UPPERCASE | Default formatting |
| Container rows | Find the row with headers "NO", "FT", "CONTAINER NO" etc. Insert or delete rows below it to match `container_quantity`. Each row: col B = row index (1, 2...), col C = `"{size_short}HQ"`. Leave cols D (CONTAINER NO), E (SEAL NO), H (TARE WEIGHT) blank. | Rows to match `container_quantity` | Copy row format from existing row |

## 10.2 SI Sheet

| Field | How to Locate | Value to Set | Formatting |
|---|---|---|---|
| SI number (title) | Find cell containing "SHIPPING INSTRUCTION" in row 1 or 2 | `"SHIPPING INSTRUCTION - {seq}{MM}{YYYY}"` | Default formatting |
| Container rows (goods) | Find row with "DESCRIPTION OF GOODS" header. Adjust rows below to match `container_quantity`. Each container gets one row with BAGS, N.W, G.W columns. CONT NO and SEAL NO columns are formula-referenced from VGM sheet — leave as-is after row insertion. | Rows to match `container_quantity` | Copy row format from existing rows |
| ETD | Find cell containing "ETD" in column B; set the adjacent cell | `"{dd} {MMMM YYYY}"` formatted from `etd_belawan` (e.g. "03 AUGUST 2026") | Font color: Red (RGB 255,0,0) |

### 10.2.1 LOLO Reference Table (SI Sheet)

After the standard SI fields are written, the app appends a small reference table near the bottom-right of the SI sheet. This table is for the worker to note LOLO prices on the printed hardcopy — the values are filled in by hand after printing, not by the app.

Table structure — 3 rows total, 2 columns:

| Row | Column A (Key) | Column B (Value) |
|---|---|---|
| Header row | "Indra" (merged across both columns, bold, centered) | — |
| Row 1 | "Seal & lolo mty" | Empty — left blank for handwriting. Column width: 10 Excel units (≈ 9 characters wide). |
| Row 2 | "Lolo full" | Empty — left blank for handwriting. Same column width. |

Placement — CRITICAL: the SI sheet must remain exactly 1 printed page. The table must be placed WITHIN the existing print area, in the bottom-right whitespace that already exists in the SI layout. Do NOT append rows below the last row and do NOT expand the print area.

Placement algorithm:

- Read the sheet's defined print area (`ws.api.PageSetup.PrintArea`). If no print area is set, use the sheet's `UsedRange`.
- Within the print area, scan from the bottom-right corner upward to find a 3-row × 2-column block of empty cells. "Empty" means no value, no formula, and no border.
- Place the table in that block. If no 3×2 empty block exists within the print area, log a warning and skip table insertion (do not add it outside the print area).
- Apply a thin border (`BorderStyle.THIN`) around all 6 cells. Key column: bold text, no fill. Value column: no text, light gray fill (`#F5F5F5`).

> **Note:** Tag the header cell with the named range `"LOLO_TABLE_HEADER"` so the app can reliably locate and delete the table later without searching by text content.

### 10.2.2 LOLO Table Deletion (triggered by auto-print)

Immediately after the SI sheet auto-prints successfully, the app:

- Opens the Excel file via xlwings (or connects to existing instance if already open)
- Looks up the named range `"LOLO_TABLE_HEADER"` in the SI sheet
- Deletes that cell and the 2 rows below it (3 rows total)
- Deletes the named range definition
- Saves and closes the file (or leaves open if it was already open when the app connected)
- If the named range is not found (e.g. already deleted), skip silently — no error

## 10.3 P.List Buyer Sheet

| Field | How to Locate | Value to Set | Formatting |
|---|---|---|---|
| Container rows | Find row with "DESCRIPTION OF GOODS" header. Adjust rows below to match `container_quantity`. Each container gets one row. CONT NO and SEAL NO columns are formula-referenced from VGM sheet. | Rows to match `container_quantity` | Copy row format from existing rows |

Sheets not modified: PI, Inv Buyer, Inv BC (these update via Excel formulas automatically).

---

# 11. Settings

Accessible from the sidebar gear icon. The settings screen uses a tab layout with five tabs.

## Tab 1: General

- Google Drive Root Path: text field + folder picker button + "Validate" button
- Validation checks for presence of exporter subfolders and shows result inline
- App theme: Light / Dark (default: Light)
- Email saya: dropdown with the 3 worker email addresses (`ongkalung@gmail.com`, `achentan96@gmail.com`, `edbertongko88@gmail.com`). Worker selects their own email. This is used to exclude the sender from the To field when composing emails. Also prompted during first-launch setup wizard Step 3.
- Email tim: read-only display of the 3 worker emails. All 3 are always CC'd on outgoing emails minus "email saya".

## Tab 2: LLM

- Provider dropdown: "Claude API (Anthropic)" or "Local (Ollama)"
- If Claude API: API key text field (obscured), "Test Connection" button
- If Ollama: Model name text field (default: llama3), Ollama URL (default: `http://localhost:11434`), "Test Connection" button
- "Test Connection" sends a short extraction test and shows result

## Tab 3: Contacts

A structured form for all recurring contacts. Organized by role:

| Contact Role | Fields | Notes |
|---|---|---|
| Nanda (document handler) | Email | — |
| Gucimas (fumigator) | Email | — |
| Pestcindo (fumigator) | Email | — |
| Toni (field manager) | WhatsApp number (with country code) | — |
| Indra (LOLO payments) | WhatsApp number | — |
| Shipping Companies | Name, Email, WhatsApp number | Multiple entries supported. Each entry shown as a row with add/remove. The active shipment references a specific shipping company (selectable per-shipment). |
| Exporter Heads | One per exporter code. Name, WhatsApp number. | Pre-populated with exporter codes (INDO, AMJ, TTJ, TASHA, NIT, THREESTAR). User fills in name and WA number. |

## Tab 4: Message Templates

A list of all 16 message templates (T01–T16). Each row shows: Template ID, step name, channel icon (email/WhatsApp). Clicking a row opens a template editor panel:

- Subject field (email templates only)
- Body text area with syntax highlighting for `{placeholder}` variables
- A reference panel on the right listing all available `{variables}` with descriptions
- "Preview" button — shows the template rendered with dummy shipment data
- "Reset to Default" button — restores the original default template
- "Save" button

## Tab 5: Quarantine Countries

An editable list of destination countries that require a quarantine check. Default list contains: PAKISTAN. User can add or remove countries. This list is checked during Step 2 of the New Shipment Wizard.

---

# 12. Shipment History

Accessible from the sidebar "History" link. Shows all shipments with status = "Completed".

## 12.1 Layout

- Search bar at the top (filters by exporter, vessel name, booking number, destination)
- Filter chips: by exporter code (INDO, AMJ, TTJ, TASHA, NIT, THREESTAR), by year
- Results shown as a sortable table:

| Column | Content |
|---|---|
| Exporter | Colored badge |
| Seq # | Sequence number |
| Vessel / Voyage | Vessel name + voyage code |
| Booking No. | Booking number |
| Destination | Destination port |
| ETD | ETD date |
| Completed | Completion date |
| Actions | "Open Folder" button → opens folder in Windows Explorer |

> **Note:** The app stores only metadata for history records. All documents remain in Google Drive. If the folder has been moved or deleted from Drive, the "Open Folder" button will show an error.

---

# 13. Data Model (SQLite)

## 13.0 Database File Location & Multi-User Sync

The database file is stored at:

```
{google_drive_root}/JANGAN DISENTUH/exportmgr.db
```

The "JANGAN DISENTUH" folder is created automatically on first launch if it does not exist. This folder lives inside the shared Google Drive, which means all workers whose machines have Google Drive synced locally will automatically share the same database — no external server or cloud account required beyond the existing Google Drive setup.

On startup the app opens the database with the following pragmas:

```sql
PRAGMA journal_mode=WAL;   -- enables concurrent reads alongside writes
PRAGMA busy_timeout=3000;  -- wait up to 3s if another writer holds a lock
```

If the DB cannot be opened after the busy timeout (e.g. another worker is mid-write), the app shows a non-blocking toast: "Database is busy — another user may be writing. Retrying…" and retries automatically. With 3 concurrent users performing light writes (marking steps complete, occasionally adding a shipment), lock collisions will be rare. The worst case is a 3-second delay.

The "JANGAN DISENTUH" folder name serves as a visual deterrent so workers do not accidentally move or delete the database file. The app never exposes this path in its UI. If the folder or file is missing on a subsequent launch, the app treats it as a first-time setup and re-runs the setup wizard.

## 13.1 Table: shipments

| Column | Type | Description |
|---|---|---|
| id | TEXT PRIMARY KEY | UUID |
| exporter_code | TEXT | INDO \| AMJ \| TTJ \| TASHA \| NIT \| THREESTAR |
| sequence_number | INTEGER | Shipment sequence number for this exporter |
| booking_number | TEXT | — |
| vessel_name | TEXT | — |
| voyage | TEXT | — |
| etd_belawan | TEXT | ISO date string YYYY-MM-DD |
| destination_port | TEXT | — |
| destination_country | TEXT | — |
| container_quantity | INTEGER | — |
| container_size_short | TEXT | e.g. 40' |
| empty_pickup_location | TEXT | Nullable |
| quarantine_required | INTEGER | Boolean: 0 or 1 |
| folder_path | TEXT | Absolute local path to shipment folder |
| do_pdf_filename | TEXT | Original DO PDF filename |
| shipping_company_id | TEXT | FK → contacts table, nullable |
| status | TEXT | active \| completed |
| created_at | TEXT | ISO datetime |
| completed_at | TEXT | ISO datetime, nullable |

## 13.2 Table: workflow_steps

| Column | Type | Description |
|---|---|---|
| id | TEXT PRIMARY KEY | UUID |
| shipment_id | TEXT | FK → shipments.id |
| step_code | TEXT | e.g. A1, B4, C3 |
| status | TEXT | pending \| complete \| skipped |
| completed_at | TEXT | ISO datetime, nullable |
| completion_source | TEXT | auto \| manual — how it was marked complete |

## 13.3 Table: settings

| Column | Type | Description |
|---|---|---|
| key | TEXT PRIMARY KEY | Setting key name |
| value | TEXT | Setting value (JSON-encoded where complex) |

## 13.4 Table: contacts

| Column | Type | Description |
|---|---|---|
| id | TEXT PRIMARY KEY | UUID |
| role | TEXT | nanda \| gucimas \| pestcindo \| toni \| indra \| shipping_company \| exporter_head |
| name | TEXT | Display name |
| email | TEXT | Nullable |
| whatsapp | TEXT | Nullable — international format with country code |
| exporter_code | TEXT | For role=exporter_head: which exporter this contact belongs to. Nullable otherwise. |
| shipping_company_name | TEXT | For role=shipping_company only. Nullable otherwise. |

## 13.5 Table: message_templates

| Column | Type | Description |
|---|---|---|
| id | TEXT PRIMARY KEY | Template ID (T01–T16) |
| step_code | TEXT | Associated workflow step code (e.g. C3) |
| channel | TEXT | email \| whatsapp |
| subject_template | TEXT | Email subject with `{placeholders}`. Null for WhatsApp. |
| body_template | TEXT | Message body with `{placeholders}` |

---

# 14. Error Handling

| Scenario | Behavior |
|---|---|
| Google Drive path not found or not accessible | Show error banner in Setup / Settings: "The configured Google Drive path does not exist or is not accessible. Please update the path in Settings." |
| Exporter subfolder not found in Drive | Show warning: "No [EXPORTER] folder found in your Google Drive. The folder structure may be different. Please check Settings." |
| No previous shipment folder to copy from | Show dialog: "No existing shipment folder found for [EXPORTER]. Please select a folder to use as the template." → folder picker opens. |
| New folder already exists (name collision) | Show error: "A folder named [folder name] already exists. Increment the sequence number or check existing folders." Do not proceed. |
| LLM API key invalid / network error | Show error in Step 1 after clicking Next: "Could not connect to the LLM. Check your API key in Settings or your internet connection." Offer to proceed with manual entry. |
| LLM returns invalid or incomplete JSON | Proceed to Step 2 with empty or partially filled fields and show warning banner: "Some fields could not be extracted automatically. Please fill them in manually." |
| Excel file not found in new folder | Show error: "Could not find the Excel file in the new folder. Please check that the template folder contains a VGM/SI/Inv/PL Excel file." |
| Microsoft Excel not installed | Show error: "Microsoft Excel is required to pre-fill the documents. Please install Excel and try again." |
| Excel file is locked (open by another process) | xlwings will connect to the existing Excel instance. If the file is locked by another user (network lock), show: "The Excel file is locked by another user. Close it and try again." |
| Subfolder missing from copied folder | Log a warning, skip clearing that subfolder, continue with remaining operations. |
| WhatsApp Desktop not installed | Silently fall back to WhatsApp Web URL. No error shown. |
| Gmail URL too long for browser | Some browsers truncate very long URLs. If the body exceeds 1800 characters after URL-encoding, truncate the body and append "[Message truncated — paste full text manually]". |

---

# 15. Phase 2 Preview

These features are not part of the Phase 1 build but are described here to ensure the Phase 1 architecture supports them without requiring structural changes.

| Feature | Description |
|---|---|
| Container & Seal Number Entry | Input fields in step C1 of Shipment Detail. On submit, the app uses xlwings to write container numbers and seal numbers into the VGM and SI sheets. |
| BNCT Auto-Monitor | A background thread periodically POSTs to the three BNCT monitoring endpoints (no auth required). Parses the HTML response to find the vessel by name+voyage and reads the Loading → Remain → Total value. When < 5, shows a system notification: "Vessel [name] is departing — pay LOLO full to Indra." Monitoring interval is configurable (default: 15 minutes). Starts automatically when step D2 is reached. |
| BL Confirmation & Excel Update | Input fields in step E2 for the confirmed date and ETD. On submit, app uses xlwings to update all sheets with the confirmed values. |
| PDF Export | Step E4 button: uses xlwings to export SI, VGM, Inv Buyer, and P.List Buyer sheets as individual PDFs into the shipment's PDF subfolder. |
| Email Inbox LLM Scanning | Optional integration: app polls Gmail API for emails matching the shipment (by booking number). Uses LLM to detect whether the email contains a PEB, COO, fumigation certificate, or BL draft. Auto-marks corresponding steps complete. Requires Gmail OAuth setup. |

---

# 16. Open Items

| # | Item | Status |
|---|---|---|
| 1 | Message template body text (T01–T16): default bodies to be provided by user once actual message examples are shared. This is the final action item before the app is fully operational — all infrastructure will be built and working; only the default message text will be missing. | Pending — final action item, user to provide last |
| 2 | Exporter full company names: the `{exporter_full}` template variable in Settings is optional. Only needed if message template bodies reference the formal company name (e.g. "atas nama PT. ..."). User can fill these in later or leave blank if templates do not use `{exporter_full}`. | Optional — fill in Settings after app is built |
| 3 | LOLO prices: handled manually. A reference table (header: "Indra", rows: "Seal & lolo mty" / "Lolo full") is added to the SI sheet during pre-fill. Worker handwrites prices on the printout. Table is auto-deleted when step B2 is marked complete. No file lookup required. | Resolved — implemented in Section 10.2.1 |

---

*Bot Kalung PRD · v0.3 · Draft*
