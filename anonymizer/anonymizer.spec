# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the GDPR Anonymizer.

Build a self-contained desktop app (no Python install needed):

    pip install pyinstaller
    pyinstaller --noconfirm anonymizer.spec

Outputs:
    dist/Anonymizer.app       (macOS, double-clickable)
    dist/Anonymizer/          (Windows/Linux; run Anonymizer.exe)
"""

from pathlib import Path

root = Path(SPECPATH)          # anonymizer/
repo = root.parent             # bank-compliance-suite/ (shared core lives here)

a = Analysis(
    ["app.py"],
    pathex=[str(repo)],                       # so 'core' is importable at analysis time
    hiddenimports=["core.audit"],
    datas=[(str(repo / "core"), "core")],     # ship shared core into the bundle
    excludes=["core.pdf_loader", "core.verification", "core.table_processor",
              "core.llm_client"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Anonymizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Anonymizer",
)

app = BUNDLE(
    coll,
    name="Anonymizer.app",
    icon=None,
    bundle_identifier="com.bank-compliance.anonymizer",
    info_plist={
        "CFBundleName": "Offline GDPR Anonymizer",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable": True,
    },
)