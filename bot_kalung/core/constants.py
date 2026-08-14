"""Static domain constants shared across the app.

UI-facing strings live in Bahasa Indonesia (see PRD Section 1.4); the identifiers
themselves stay in English.
"""

APP_NAME = "Bot Kalung"

# ntfy push notifications. The topic is a single fixed value shared by every
# install, so the whole team subscribes to the one feed
# (https://ntfy.sh/<topic> in the ntfy app). It is intentionally random and not
# user-editable; only the enabled toggle and server live in Settings.
NTFY_TOPIC = "botkalung-lolo-9f0dadc10541b6cebb56"
NTFY_DEFAULT_SERVER = "https://ntfy.sh"

# PRD Section 1.1 lists six exporters. Two changes confirmed with the user
# 2026-07-20: TASHA and TTJ are one business sharing a folder and sequence series
# (every file on disk uses the TTJ prefix), and INDO is out of scope entirely —
# it keeps loose PDFs instead of the VGM/SI/Inv/PL workbook the app automates.
EXPORTERS = ["AMJ", "TTJ", "NIT", "THREESTAR"]

# PRD Section 2.4 — badge colors, consistent everywhere the exporter is shown.
EXPORTER_COLORS = {
    "AMJ": "#2563eb",        # blue
    "TTJ": "#7c3aed",        # purple
    "NIT": "#16a34a",        # green
    "THREESTAR": "#dc2626",  # red
}

# Drive folder per exporter — only AMJ matches its own code. Overridable in
# Settings > General; these are the defaults observed on the live Drive.
DEFAULT_EXPORTER_FOLDERS = {
    "AMJ": "AMJ",
    "TTJ": "TASHA-HUSSAIN-MAJEED",
    "NIT": "NMEHMOOD & CV.Hassan",
    "THREESTAR": "Three star-waleed",
}

# Where shipment folders sit inside the exporter folder. "{year}" is filled from
# the shipment ETD; an empty string means they sit in the exporter folder root.
# TTJ's folder also holds "2026 MAJEED" and "2026 Hussain" — separate businesses
# the app must not touch, which is why this is an explicit subpath, not a scan.
DEFAULT_SHIPMENT_SUBPATH = {
    "AMJ": "{year}",
    "TTJ": "{year} Tasha",
    "NIT": "{year}",
    "THREESTAR": "",
}

# The code used inside document filenames, which is not always the exporter code
# (THREESTAR files read TSI).
DEFAULT_FILE_CODES = {
    "AMJ": "AMJ",
    "TTJ": "TTJ",
    "NIT": "NIT",
    "THREESTAR": "TSI",
}

# PRD Section 3 — the three fixed worker accounts.
WORKER_EMAILS = [
    "ongkalung@gmail.com",
    "achentan96@gmail.com",
    "edbertongko88@gmail.com",
]

# Folder-scan tracking (post-refactor): the app discovers active shipments by
# walking every exporter folder under the Drive root, so the fixed EXPORTERS list
# above no longer bounds the scan. These top-level folders are skipped — they are
# either not exporters (expenses, tax, relations) or businesses the user asked to
# leave alone. Matched case-insensitively against the folder name.
EXCLUDED_SCAN_FOLDERS = [
    "PENGELUARAN",          # expenses
    "PPH",                  # tax
    "RELASI",               # relations / contacts
    "GETAH ROMI",           # excluded by the user (2026-08)
    "LHOONG SETIA MINING",  # excluded by the user (2026-08)
]

# PRD Section 13.0 — shared database location inside Google Drive.
# The "zzz " prefix keeps the folder at the bottom of an alphabetical listing
# (plain letters, so the ordering holds in Explorer and in Drive on the web),
# and the folder is also marked hidden on Windows. Renamed from the PRD's plain
# "JANGAN DISENTUH" at the user's request on 2026-07-20.
DB_FOLDER_NAME = "zzz JANGAN DISENTUH"
DB_FILE_NAME = "exportmgr.db"

# PRD Section 9.2 step 2 — subfolders emptied on a fresh shipment copy.
SUBFOLDERS_TO_CLEAR = ["Dok kirim", "Draf", "Foto", "Fumi", "PDF", "PEB & NPE"]

# PRD Section 9.2 step 3 — root files that survive the purge. The main workbook
# and the invoice are kept by content (see fileops.is_main_workbook / is_invoice),
# since their filenames vary too much between exporters for a glob; these globs
# cover the import permits.
KEEP_FILE_PATTERNS = [
    "PERMIT-*.pdf",
    "IP-*.pdf",
]

# PRD Section 5 / 11 Tab 5 — default quarantine list, editable in Settings.
DEFAULT_QUARANTINE_COUNTRIES = ["PAKISTAN"]

# PRD Section 6.3 — full workflow definition. Phase 2 steps are listed so the
# checklist shows the whole process, but they render disabled.
WORKFLOW_PHASES = [
    ("A", "Fase A: Koordinasi Pra-Pengiriman"),
    ("B", "Fase B: Persiapan Dokumen"),
    ("C", "Fase C: Pengiriman Dokumen"),
    ("D", "Fase D: Pemantauan Kapal"),
    ("E", "Fase E: Finalisasi"),
]

WORKFLOW_STEPS = [
    # (code, phase, title, description, auto_complete, phase2_only)
    ("A1", "A", "Terima DO",
     "DO PDF diterima dan pengiriman dibuat", True, False),
    ("A2", "A", "Tanya eksportir: pembeli & gudang",
     "WhatsApp kepala eksportir untuk nama pembeli, lokasi gudang, dan dokumen khusus pembeli",
     True, False),
    ("A3", "A", "Tanya pengirim: jadwal kapal",
     "Minta konfirmasi jadwal kapal via email dan/atau WhatsApp", True, False),
    ("A4", "A", "Tanya pelayaran: kontainer kosong",
     "Minta jadwal pengambilan kontainer kosong", True, False),

    ("B1", "B", "Setup selesai",
     "Folder dibuat dan Excel terisi otomatis", True, False),
    ("B2", "B", "Cetak SI",
     "Lembar SI dicetak otomatis setelah Excel terisi", True, False),
    ("B3", "B", "Beritahu Toni (manajer lapangan)",
     "Kirim ke Toni: tanggal & lokasi ambil kontainer, jadwal ambil barang, jadwal stuffing. Lampirkan DO manual.",
     True, False),
    ("B4", "B", "Bayar LOLO kosong ke Indra",
     "Lakukan pembayaran LOLO kosong", False, False),
    ("B5", "B", "Email fumigator",
     "Kirim SI dan dokumen izin impor ke fumigator (Gucimas atau Pestcindo)", True, False),
    ("B6", "B", "Email Nanda: SI, VGM, Invoice, PL",
     "Kirim dokumen ke Nanda untuk persiapan PEB dan COO", True, False),

    ("C1", "C", "Terima nomor kontainer & segel",
     "Tunggu Toni mengirim nomor kontainer dan segel. Masukkan ke Excel secara manual.",
     False, False),
    ("C2", "C", "Email pelayaran: SI + VGM",
     "Kirim SI dan VGM ke perusahaan pelayaran", True, False),
    ("C3", "C", "Email PEB ke pelayaran",
     "Setelah menerima PEB dari Nanda, teruskan ke perusahaan pelayaran", True, False),

    ("D1", "D", "Tunggu sertifikat fumigasi",
     "Fumigator mengirim sertifikat fumigasi", False, False),
    ("D2", "D", "Pantau BNCT: kapal sandar",
     "Cek portal BNCT untuk status 'Vessel Alongside'. Jika Loading Remain Total < 5, kapal akan berangkat.",
     False, False),
    ("D3", "D", "Bayar LOLO penuh ke Indra",
     "Lakukan pembayaran LOLO penuh setelah kapal sandar", False, False),

    ("E1", "E", "Minta draf BL",
     "Minta draf Bill of Lading ke perusahaan pelayaran", True, False),
    ("E2", "E", "Revisi & konfirmasi BL",
     "Periksa draf BL, minta revisi bila perlu, konfirmasi final", False, False),
    ("E3", "E", "Perbarui Excel: tanggal & ETD final",
     "Perbarui tanggal dan ETD di semua sheet sesuai BL yang dikonfirmasi", False, False),
    # Pulled forward from Phase 2 at the user's request (2026-07-20).
    ("E4", "E", "Ekspor ke PDF",
     "Ekspor sheet SI, VGM, Invoice, PL ke PDF di subfolder PDF", False, False),
    ("E5", "E", "Cetak dokumen",
     "Cetak Invoice & PL (4 rangkap), Phyto (1), COO (1), sertifikat fumigasi (1)",
     False, False),
    ("E6", "E", "Beritahu kepala eksportir",
     "Kirim notifikasi final ke kepala eksportir bahwa pengiriman selesai", True, False),
]

STEP_CODES = [s[0] for s in WORKFLOW_STEPS]

# PRD Section 6.3 — action buttons per step.
# (label, kind, payload). kind: "template" -> payload is a template id;
# "url" -> payload is a link; "reprint" -> re-run the SI print.
#
# Decided 2026-07-20: WhatsApp steps no longer compose a pre-filled message.
# Their button just brings WhatsApp Web to the front (or opens it), and the
# worker writes and sends the message themselves, then ticks the box.
# Communication buttons are labelled just "Email" / "WhatsApp" — not by
# recipient (user, 2026-07-21). Each step keeps the channel(s) it needs. B5
# used to have two email buttons (Gucimas, Pestcindo); since both now compose
# the same draft (subject "{exporter}{seq}", empty body, the two teammates),
# they collapse into one "Email" button and the worker adds recipients.
STEP_ACTIONS: dict[str, list[tuple[str, str, str]]] = {
    "A2": [("WhatsApp", "whatsapp", "")],
    "A3": [("WhatsApp", "whatsapp", "")],
    "A4": [("WhatsApp", "whatsapp", "")],
    "B2": [("Cetak Ulang SI", "reprint", "")],
    "B3": [("WhatsApp", "whatsapp", "")],
    "B4": [("WhatsApp", "whatsapp", "")],
    "B5": [("Email", "template", "T08")],
    "B6": [("Email", "template", "T10")],
    "C2": [("Email", "template", "T11")],
    "C3": [("Email", "template", "T12")],
    "D2": [("Buka Portal BNCT", "url", "https://portal.bnct-id.com/sso/")],
    "D3": [("WhatsApp", "whatsapp", "")],
    "E1": [("Email", "template", "T14"),
           ("WhatsApp", "whatsapp", "")],
    "E3": [("Buka Excel", "excel", "")],
    "E4": [("Ekspor PDF", "pdf", "")],
    "E6": [("WhatsApp", "whatsapp", "")],
}

# Steps whose action button marks the step complete when clicked (PRD 6.3
# "Auto-complete?" column). Only the email buttons qualify: opening WhatsApp or
# the BNCT portal is not evidence the work was done, so those stay manual.
STEPS_COMPLETED_BY_ACTION = {"B5", "B6", "C2", "C3", "E1"}

# Buttons that must never complete their step, even on a step that is otherwise
# auto-completed by another button (A3, A4 and E1 have both kinds).
NON_COMPLETING_ACTION_KINDS = {"whatsapp", "url", "reprint", "excel",
                              "pdf"}

# Re-checking these re-runs a side effect, so they confirm first (PRD 6.2).
STEPS_WITH_SIDE_EFFECTS = {
    "B2": ("Cetak ulang lembar SI?",
           "Lembar SI akan dicetak ulang dari file Excel."),
}
