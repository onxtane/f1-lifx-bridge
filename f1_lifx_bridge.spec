# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for GridGlow (F1 LIFX Bridge)
# Build:  pyinstaller f1_lifx_bridge.spec
#
# Windows → dist\F1LifxBridge\F1LifxBridge.exe  (folder mode)
# macOS   → dist/GridGlow.app                   (.app bundle, Cocoa backend)

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_all

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Keep in sync with the UI/website version on release.
APP_VERSION = "0.8.0"

block_cipher = None

# ── Collect lifxlan and bitstring (dynamic imports missed by analysis) ────────
lifxlan_datas, lifxlan_binaries, lifxlan_hiddenimports = collect_all('lifxlan')
bitstring_datas, bitstring_binaries, bitstring_hiddenimports = collect_all('bitstring')

# ── Data files (cross-platform) ──────────────────────────────────────────────
datas = [
    ('ui/index.html', 'ui'),
    *collect_data_files('webview'),       # picks up the platform's webview assets
    *collect_data_files('nanoleafapi'),
    *lifxlan_datas,
    *bitstring_datas,
]

# ── Hidden imports (cross-platform core) ─────────────────────────────────────
hidden_imports = [
    'lifxlan', 'lifxlan.lifxlan', 'lifxlan.device', 'lifxlan.light',
    'lifxlan.multizonelight', 'lifxlan.group', 'lifxlan.message',
    'lifxlan.msgtypes', 'lifxlan.products', 'lifxlan.unpack', 'lifxlan.utils',
    'lifxlan.errors', 'lifxlan.tilechain', 'lifxlan.switch',
    *lifxlan_hiddenimports,
    *bitstring_hiddenimports,
    'bitstring', 'bitstring.bitstore_bitarray',
    *collect_submodules('nanoleafapi'),
    'requests', 'urllib3', 'charset_normalizer', 'certifi', 'idna',
    'colorsys', 'ipaddress',
]

# ── Platform-specific backend + excludes ─────────────────────────────────────
common_excludes = ['tkinter', 'matplotlib', 'numpy', 'pandas', 'scipy',
                   'webview.platforms.cef']

if IS_WIN:
    hidden_imports += [
        'webview.platforms.qt',
        'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore',
        'PySide6.QtWebChannel', 'PySide6.QtNetwork', 'PySide6.QtPositioning',
    ]
    excludes = common_excludes + ['webview.platforms.winforms',
                                  'webview.platforms.gtk', 'webview.platforms.cocoa']
elif IS_MAC:
    # Native Cocoa / WKWebView backend (pyobjc) — no Qt on macOS.
    hidden_imports += [
        'webview.platforms.cocoa',
        'objc', 'Foundation', 'AppKit', 'WebKit', 'Quartz',
    ]
    excludes = common_excludes + ['webview.platforms.winforms',
                                  'webview.platforms.gtk', 'webview.platforms.qt',
                                  'PySide6', 'PyQt5', 'PyQt6']
else:
    hidden_imports += ['webview.platforms.gtk']
    excludes = common_excludes + ['webview.platforms.winforms',
                                  'webview.platforms.cocoa', 'webview.platforms.qt']

# ── Analysis ──────────────────────────────────────────────────────────────────

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[*lifxlan_binaries, *bitstring_binaries],
    datas=datas,
    hiddenimports=hidden_imports,
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

# Windows keeps the historical name/artifacts; macOS uses the product name.
exe_name = 'GridGlow' if IS_MAC else 'F1LifxBridge'

# ── EXE (folder mode — faster startup, easier debugging) ─────────────────────

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # folder mode: binaries go into COLLECT below
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=not IS_MAC,             # UPX can corrupt dylibs / break codesigning on macOS
    console=False,              # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                  # add an .ico (win) / .icns (mac) once available
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=not IS_MAC,
    upx_exclude=[],
    name=exe_name,
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name='GridGlow.app',
        icon=None,
        bundle_identifier='dev.gridglow.app',
        version=APP_VERSION,
        info_plist={
            'CFBundleName': 'GridGlow',
            'CFBundleDisplayName': 'GridGlow',
            'CFBundleShortVersionString': APP_VERSION,
            'CFBundleVersion': APP_VERSION,
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '11.0',
            'NSPrincipalClass': 'NSApplication',
            # macOS 14+ blocks LAN/UDP/mDNS without this — required for light discovery.
            'NSLocalNetworkUsageDescription':
                'GridGlow finds and controls your LIFX, Nanoleaf, and Hue lights '
                'over your local network.',
        },
    )
