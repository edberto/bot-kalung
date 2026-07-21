# Bot Kalung

Windows desktop app for managing broomstick export shipments — folder setup,
Excel pre-fill, workflow tracking, and email drafting.

See `PRD.md` for the specification and `DECISIONS.md` for every place the build
deliberately departs from it, with the reasoning.

## Running from source

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m bot_kalung.main
```

Requires Microsoft Excel (xlwings drives it over COM) and Windows.

## App icon

`logo.jpeg` in the project root is the source. After replacing it, regenerate
the icon and rebuild:

```
.venv\Scripts\python tools\make_icon.py
.venv\Scripts\pyinstaller --noconfirm bot_kalung.spec
```

The artwork is landscape, so it is padded to a square with the background
colour sampled from just inside its own border, then written to
`bot_kalung\assets\icon.ico` at every size Windows asks for (16 to 256 px).
That file becomes both the executable's icon and the window/taskbar icon.

## Building the executable

```
.venv\Scripts\pyinstaller --noconfirm --clean bot_kalung.spec
```

Produces `dist\Bot Kalung.exe` — a single ~70 MB file with no installer and no
Python needed on the target machine.

## Distributing

There is no installer, by design: `dist\Bot Kalung.exe` is the whole program.
Copy it wherever it should live — a Drive folder, a USB stick, the desktop — and
double-click it. Nothing is written to Program Files or the registry.

Updating means replacing that one file. Since there is no installed second copy,
there is no risk of a worker running a stale version.

Two things to expect on a machine that has not run it before:

* **SmartScreen** shows "Windows protected your PC" the first time. Choose
  *More info* then *Run anyway*. This happens because the executable is not
  code-signed; suppressing it requires a paid certificate.
* **Microsoft Excel must be installed** — the pre-fill drives it over COM.

The app keeps no per-machine state beyond `%LOCALAPPDATA%\BotKalung\bootstrap.json`,
which holds only the Google Drive path. Delete that file to make the setup
wizard run again.

## Verifying a build

Check a build without opening the UI:

```
dist\"Bot Kalung.exe" --selftest
```

It imports every runtime dependency, checks the pdfminer character maps and the
certifi CA bundle are bundled, and writes `selftest.log` beside the executable.
Exit code 0 means the build is complete. This catches a missing data file or
hidden import, which otherwise only surfaces when a worker hits that feature.

If the app ever fails to start, `botkalung-error.log` appears next to the
executable with the traceback — a frozen GUI build has no console to print to.

## Tests

```
.venv\Scripts\python tests\test_phase1.py
```

Each file is standalone and prints one line per check. The Excel suites
(`test_excel_amj`, `test_excel_nit`, `test_excel_ttj_tsi`) need Excel and read
the live templates from Google Drive — they always work on copies in a temp
directory and never write to `G:\`.

| File | Covers |
|---|---|
| `test_phase1` | schema, WAL pragmas, settings |
| `test_phase2` | setup wizard, contacts, LLM error handling |
| `test_phase3` | main window, sidebar, dashboard, navigation |
| `test_phase4_ui` | new-shipment wizard, DO extraction post-processing |
| `test_detail` | shipment header, quarantine banner, folder/Excel buttons |
| `test_checklist` | 21-step checklist, action buttons, email drafts |
| `test_history` | search, filters, sorting |
| `test_settings` | all four settings tabs, persistence |
| `test_theme` | light/dark tokens and toggle |
| `test_drive` | folder scanning, sequence detection, filename derivation |
| `test_fileops` | folder operations, rollback, naming rules |
| `test_carrier` | carrier detection, booking-number extraction |
| `test_excel_*` | pre-fill against each exporter's real template |

## Data locations

| What | Where |
|---|---|
| Shared database | `{google_drive_root}\zzz JANGAN DISENTUH\exportmgr.db` |
| Drive path pointer | `%LOCALAPPDATA%\BotKalung\bootstrap.json` |

The database lives in Google Drive so all three workers share it. The folder is
hidden and prefixed to sort last. The bootstrap file is per-machine and holds
only the Drive path — it exists because the database's location is itself a
setting stored in that database.
