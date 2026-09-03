# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for LLM Usage Meter standalone binaries.

Build from the repo root:

    python -m PyInstaller --noconfirm packaging/llm_usage_meter.spec

Outputs land in ``dist/``:
  - macOS:  ``LLM Usage Meter.app``
  - Windows / Linux: ``llm-usage-meter`` (``.exe`` on Windows)
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.osx import BUNDLE
from PyInstaller.building.build_main import Analysis
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
ENTRY = ROOT / "launch.py"
ASSETS = ROOT / "assets"

APP_NAME = "LLM Usage Meter"
BIN_NAME = "llm-usage-meter"
ICON_ICNS = ASSETS / "app-icon.icns"
ICON_ICO = ASSETS / "app-icon.ico"

datas = [(str(ASSETS), "assets")]
binaries: list = []
hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
    "PySide6.QtNetwork",
    "keyring.backends",
    *collect_submodules("keyring.backends"),
]

# keyring backends are loaded dynamically; collect them explicitly.
# PySide6 plugins come from PyInstaller's built-in hooks (avoid collect_all,
# which would drag in Designer / WebEngine / etc.).
kr_datas, kr_binaries, kr_hidden = collect_all("keyring")
datas += kr_datas
binaries += kr_binaries
hiddenimports += kr_hidden

excludes = [
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
]

block_cipher = None

a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Tray app: no console window. Logging still goes to ~/.llm-usage-meter/app.log.
console = False

if sys.platform == "darwin":
    # One-dir .app is the natural macOS delivery form (Dock-less via LSUIElement).
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=BIN_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=console,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=BIN_NAME,
    )
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ICON_ICNS) if ICON_ICNS.is_file() else None,
        bundle_identifier="local.llm-usage-meter",
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": "local.llm-usage-meter",
            "CFBundleShortVersionString": "1.0.14",
            "CFBundleVersion": "1.0.14",
            "LSMinimumSystemVersion": "12.0",
            "LSUIElement": True,  # menu-bar accessory; no Dock icon
            "NSHighResolutionCapable": True,
            "NSSupportsAutomaticGraphicsSwitching": True,
        },
    )
else:
    # Single-file binary for Windows / Linux — no separate Python install needed.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name=BIN_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=console,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ICON_ICO) if ICON_ICO.is_file() else None,
    )
