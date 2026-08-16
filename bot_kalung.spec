# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for Bot Kalung (PRD Section 10 — single .exe).

Build with:  .venv\\Scripts\\pyinstaller.exe --noconfirm bot_kalung.spec

Notes on the choices below:

* `console=False` — this is a GUI app; a console window would flash on every
  launch. Errors surface in the UI, not on stdout.
* pdfminer.six ships character-map data files that pdfplumber needs at runtime,
  and certifi ships the CA bundle the Anthropic SDK needs for TLS. Both are
  collected explicitly rather than relying on hook coverage.
* xlwings talks to Excel over COM via pywin32, so its package data and the
  win32 modules are collected too.
* Qt is trimmed to the modules actually used; QtWebEngine and friends would
  otherwise add hundreds of megabytes.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [("bot_kalung/assets", "bot_kalung/assets")]
datas += collect_data_files("pdfminer")
datas += collect_data_files("certifi")
datas += collect_data_files("xlwings")

hiddenimports = []
hiddenimports += collect_submodules("anthropic")
# The folder scanner reads a shipment's BL PDF to detect completion. pdfplumber
# used to be pulled in transitively by the (now-removed) DO parser; import it
# explicitly so the static scan keeps it in the bundle.
hiddenimports += collect_submodules("pdfplumber")
# The headless workbook reader uses openpyxl (.xlsx) and xlrd (.xls); xlrd is
# imported lazily inside a function, which the static scan can miss.
hiddenimports += collect_submodules("openpyxl")
hiddenimports += ["xlrd", "xlrd.xldate"]
hiddenimports += [
    "win32com.client",
    "win32com.shell",   # SHFileOperation, used to delete folders to the Recycle Bin
    "win32api",
    "pythoncom",
    "pywintypes",
    # requests is imported lazily inside bnct.py (BNCT monitoring), which
    # PyInstaller's static scan can miss; pull it and its deps in explicitly.
    "requests",
    "urllib3",
    "charset_normalizer",
    "idna",
]

# Qt modules the app never touches, plus science/plotting stacks that some
# dependency chains pull in. Excluding them keeps the executable manageable.
excludes = [
    "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQuick",
    "PyQt6.QtQml", "PyQt6.QtMultimedia", "PyQt6.QtNetwork", "PyQt6.Qt3DCore",
    "PyQt6.QtCharts", "PyQt6.QtDataVisualization", "PyQt6.QtBluetooth",
    "PyQt6.QtDesigner", "PyQt6.QtTest", "PyQt6.QtSql",
    "tkinter", "matplotlib", "scipy", "numpy.distutils",
    "IPython", "jupyter", "notebook", "pytest",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Bot Kalung",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX often trips antivirus heuristics on Windows
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # GUI app: no console window
    icon="bot_kalung/assets/icon.ico",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
