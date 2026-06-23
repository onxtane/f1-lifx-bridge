"""Cross-platform resource and user-data paths for GridGlow.

Single source of truth so settings, light groups, and webview storage land in the
right place on each OS, while bundled read-only resources (ui/, tests/) are read
from the PyInstaller bundle:

  - Windows  → next to the .exe (portable; unchanged from previous behaviour)
  - macOS    → ~/Library/Application Support/GridGlow  (a .app bundle is read-only)
  - Linux    → $XDG_CONFIG_HOME/GridGlow  (or ~/.config/GridGlow)
  - dev      → the repo root (unchanged)
"""
import os
import sys
from pathlib import Path

_FROZEN = getattr(sys, "frozen", False)


def bundle_dir() -> Path:
    """Directory holding bundled read-only resources (ui/, tests/)."""
    if _FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    """Writable per-user directory for settings, groups and webview storage.

    Created if missing. Windows keeps the portable behaviour (beside the
    executable); macOS and Linux follow platform conventions so we never write
    inside a read-only/signed .app bundle.
    """
    if not _FROZEN:
        base = Path(__file__).resolve().parent           # repo root in dev
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "GridGlow"
    elif sys.platform == "win32":
        base = Path(sys.executable).parent               # portable: beside the .exe
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".config") / "GridGlow"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return base


BUNDLE_DIR = bundle_dir()
USER_DATA_DIR = user_data_dir()
