# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for F1 LIFX Bridge
# Build:  pyinstaller f1_lifx_bridge.spec
# Output: dist\F1LifxBridge\F1LifxBridge.exe  (folder mode)
#         dist\F1LifxBridge.exe                (single-file mode — slower cold start)

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_all

block_cipher = None

# ── Collect lifxlan (flat package — collect_submodules misses it) ─────────────
lifxlan_datas, lifxlan_binaries, lifxlan_hiddenimports = collect_all('lifxlan')

# ── Data files ───────────────────────────────────────────────────────────────

datas = [
    # The web UI
    ('ui/index.html', 'ui'),

    # pywebview needs its web-engine assets at runtime
    *collect_data_files('webview'),

    # nanoleafapi ships a JSON colour table that must travel with the package
    *collect_data_files('nanoleafapi'),

    # lifxlan source files (collect_all picks up .py sources too)
    *lifxlan_datas,
]

# ── Hidden imports ────────────────────────────────────────────────────────────
# PyInstaller's static analysis misses dynamically-loaded modules.

hidden_imports = [
    # pywebview Qt backend (we force PYWEBVIEW_GUI=qt in main.py)
    'webview.platforms.qt',

    # PySide6 modules pulled in by pywebview at runtime
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebChannel',
    'PySide6.QtNetwork',
    'PySide6.QtPositioning',

    # lifxlan — explicit list of every module (collect_all catches the rest)
    'lifxlan',
    'lifxlan.lifxlan',
    'lifxlan.device',
    'lifxlan.light',
    'lifxlan.multizonelight',
    'lifxlan.group',
    'lifxlan.message',
    'lifxlan.msgtypes',
    'lifxlan.products',
    'lifxlan.unpack',
    'lifxlan.utils',
    'lifxlan.errors',
    'lifxlan.tilechain',
    'lifxlan.switch',
    *lifxlan_hiddenimports,

    # nanoleafapi
    *collect_submodules('nanoleafapi'),

    # requests / urllib3 internals
    'requests',
    'urllib3',
    'charset_normalizer',
    'certifi',
    'idna',

    # Standard-library modules sometimes missed on Windows
    'colorsys',
    'ipaddress',
]

# ── Analysis ──────────────────────────────────────────────────────────────────

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[*lifxlan_binaries],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Remove unused GUI backends to cut size
        'webview.platforms.winforms',
        'webview.platforms.cef',
        'webview.platforms.gtk',
        'webview.platforms.cocoa',
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE (folder mode — faster startup, easier debugging) ─────────────────────

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # folder mode: binaries go into COLLECT below
    name='F1LifxBridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                   # compress where possible
    console=False,              # no CMD window — change to True to see logs
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                  # add an .ico path here once you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='F1LifxBridge',
)
