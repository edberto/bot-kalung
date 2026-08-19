# Tests

The suite is split by which app the test belongs to, so the **server worker** can
be validated without the desktop stack (PyQt6 / Excel / xlwings):

| Folder | Covers | Deps |
|--------|--------|------|
| `server/` | Server + shared code — the folder-scan ingest, BNCT parsing/monitoring, containers, notifications, workbook/excel pure parsers, schema migration. **No PyQt6, no `bot_kalung.ui`.** | `requirements-server.txt` (+ openpyxl/xlrd for the workbook tests) |
| `desktop/` | The PyQt6 UI app and desktop-only services (fileops, resequence, etd_change, pdf_export, printing, messaging, whatsapp, llm, search) and their views. | `requirements.txt` (PyQt6, xlwings, …) |

Shared, at the `tests/` root: `sandbox.py` (redirects the machine-local bootstrap
pointer into a temp dir — imported first by every test) and `fixtures/` (BNCT
HTML captures used by `desktop/test_bnct.py`).

## Running

```bash
python tools/ci.py --only test                    # both suites
python tools/ci.py --only test --suite server     # server suite only (PyQt6-free)
python tools/ci.py --only test --suite desktop    # desktop suite only
python tools/ci.py --fast                          # full gate, Excel/Drive tests skipped (git hook)
```

Each test is a standalone script (run as `python tests/<suite>/test_x.py`): it
prepends the repo root and `tests/` to `sys.path`, imports `sandbox`, then the
code under test. Success prints `… all checks passed`; failure exits non-zero.

## Adding a test

Put it in `server/` if it imports only `bot_kalung.core.*` (db/pg/settings/
constants/templates/context/bootstrap — all PyQt6-free) and server-closure
`bot_kalung.services.*`. Put it in `desktop/` if it imports `PyQt6`,
`bot_kalung.ui.*`, or a desktop-only service. Start from the `sys.path` +
`import sandbox` preamble of a neighbouring test.
