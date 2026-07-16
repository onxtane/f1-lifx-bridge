"""Startup gate for the Windows rendering stack (#72).

The Windows build draws its window with WebView2 (pywebview's `edgechromium`
backend), reached through pythonnet/`clr`. Both halves are OS components we
deliberately don't bundle, and when either is missing pywebview does not fail
cleanly:

  - .NET Framework absent -> `webview.platforms.winforms` raises UnboundLocalError
    while being imported: its `_is_chromium()` closes a registry key it never
    managed to open. That isn't an ImportError, so pywebview's own handler misses
    it and it escapes `webview.start()`. A windowed build has no console, so the
    traceback goes nowhere and the exe just appears to do nothing.
  - WebView2 absent -> `_is_chromium()` returns False and pywebview quietly falls
    back to MSHTML (IE11) *even though* we asked for edgechromium. Our UI is
    modern Chromium-only markup, so it renders broken rather than not at all,
    which is harder to diagnose than a crash.

So we answer the same question pywebview asks, before handing it control, and
explain the problem in a native dialog — WebView2 is precisely what's broken, so
the app cannot render its own error message.

Stdlib only, on purpose: this module has to keep working on a machine where the
rest of the rendering stack doesn't.
"""
import os
import sys
from typing import Callable, NamedTuple, Optional, Tuple

# Mirrors pywebview's thresholds (webview/platforms/winforms.py::_is_chromium).
# These must agree with pywebview's verdict: if it would have rendered fine, we
# must not be the reason the app refuses to start.
_MIN_DOTNET_RELEASE = 394802               # .NET Framework 4.6.2
_MIN_WEBVIEW2_VERSION = (86, 0, 622, 0)

_DOTNET_KEY = r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"

# EdgeUpdate client GUIDs, one per WebView2 channel. pywebview accepts any of
# them, so we do too — a machine carrying only the Beta channel still renders.
_WEBVIEW2_CLIENTS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",  # Evergreen Runtime
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",  # Beta
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",  # Dev
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",  # Canary
)

_WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
_DOTNET_URL = "https://dotnet.microsoft.com/download/dotnet-framework"

# If these probes are ever wrong, a user must still be able to run an app that
# would have worked. Undocumented in the UI; it exists for support cases.
SKIP_ENV_VAR = "GRIDGLOW_SKIP_RUNTIME_CHECK"


class Problem(NamedTuple):
    """A missing dependency, described in the user's terms rather than ours."""

    title: str
    message: str
    url: str


def _read_value(hive, path: str, name: str):
    """Read one registry value, or None if it isn't there.

    Unlike pywebview's version of this, the key is only closed if it opened.
    """
    import winreg

    try:
        with winreg.OpenKey(hive, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def _parse_version(raw) -> Optional[Tuple[int, ...]]:
    """'150.0.4078.65' -> (150, 0, 4078, 65), or None if it isn't a version."""
    if not isinstance(raw, str):
        return None
    try:
        return tuple(int(part) for part in raw.split("."))
    except ValueError:
        return None


def _dotnet_release() -> Optional[int]:
    """The .NET Framework 4.x release number, or None if it isn't installed."""
    import winreg

    value = _read_value(winreg.HKEY_LOCAL_MACHINE, _DOTNET_KEY, "Release")
    return value if isinstance(value, int) else None


def _webview2_version() -> Optional[Tuple[int, ...]]:
    """Highest WebView2 version registered across channels, or None if absent."""
    import winreg

    # pywebview reads the plain HKLM path only on x86 hosts and WOW6432Node
    # elsewhere. We read every location it might, plus the one it skips, so our
    # answer can only ever be more permissive than its — a false "it's missing"
    # would lock someone out of a working app, which is the worse failure.
    locations = (
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
    )
    best = None
    for hive, prefix in locations:
        for guid in _WEBVIEW2_CLIENTS:
            found = _parse_version(_read_value(hive, prefix + "\\" + guid, "pv"))
            if found is not None and (best is None or found > best):
                best = found
    return best


def _clr_loads() -> bool:
    """True if pythonnet can actually start the CLR.

    The registry says .NET is installed; this says pythonnet can use it, which
    catches a broken or partial install the version number waves through.
    pywebview imports clr moments later anyway, so this costs us nothing.
    """
    try:
        import clr  # noqa: F401

        return True
    except Exception:
        return False


def _format_version(version: Tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def find_problem(
    dotnet_release: Optional[Callable[[], Optional[int]]] = None,
    clr_loads: Optional[Callable[[], bool]] = None,
    webview2_version: Optional[Callable[[], Optional[Tuple[int, ...]]]] = None,
) -> Optional[Problem]:
    """The thing that would stop the UI rendering, or None if nothing would.

    The probes are injectable so tests can describe a machine without having to
    touch the real registry. Checked .NET-first: it's the harder crash, and
    WebView2 needs it regardless.
    """
    release = (dotnet_release or _dotnet_release)()
    if release is None or release < _MIN_DOTNET_RELEASE:
        return Problem(
            "GridGlow - .NET Framework required",
            "GridGlow can't start because Microsoft .NET Framework 4.6.2 or newer "
            "isn't available on this PC.\n\n"
            "GridGlow uses it to draw its window. It normally comes with Windows 10 "
            "and 11, but it's missing from some stripped-down and LTSC installs.\n\n"
            "Installing it from Microsoft and starting GridGlow again should fix "
            "this.\n\n"
            "Open the download page now?",
            _DOTNET_URL,
        )

    if not (clr_loads or _clr_loads)():
        return Problem(
            "GridGlow - .NET Framework not usable",
            "GridGlow can't start because it couldn't load Microsoft .NET "
            "Framework, even though it appears to be installed.\n\n"
            "GridGlow uses it to draw its window. This usually means the .NET "
            "install is damaged or incomplete.\n\n"
            "Repairing or reinstalling it from Microsoft and starting GridGlow "
            "again should fix this.\n\n"
            "Open the download page now?",
            _DOTNET_URL,
        )

    version = (webview2_version or _webview2_version)()
    # EdgeUpdate tombstones an uninstalled runtime as pv='0.0.0.0' rather than
    # removing the key. That's "gone", not "old" — telling someone their 0.0.0.0
    # is out of date would send them looking for an update that doesn't exist.
    if version is None or not any(version):
        return Problem(
            "GridGlow - WebView2 required",
            "GridGlow can't start because the Microsoft Edge WebView2 runtime "
            "isn't installed on this PC.\n\n"
            "GridGlow uses WebView2 to draw its window. It normally comes with "
            "Windows 10 and 11, but it's missing from some stripped-down and LTSC "
            "installs.\n\n"
            "Install the free 'Evergreen Bootstrapper' from Microsoft, then start "
            "GridGlow again.\n\n"
            "Open the download page now?",
            _WEBVIEW2_URL,
        )
    if version < _MIN_WEBVIEW2_VERSION:
        return Problem(
            "GridGlow - WebView2 is too old",
            "GridGlow needs a newer Microsoft Edge WebView2 runtime to draw its "
            "window.\n\n"
            "This PC has version " + _format_version(version) + ", but GridGlow "
            "needs " + _format_version(_MIN_WEBVIEW2_VERSION) + " or newer.\n\n"
            "Updating WebView2 from Microsoft and starting GridGlow again should "
            "fix this.\n\n"
            "Open the download page now?",
            _WEBVIEW2_URL,
        )
    return None


def _show_dialog(problem: Problem) -> bool:
    """Show the problem natively. True if the user asked for the download page.

    Win32 rather than our own UI: WebView2 is the broken piece, so the app has no
    way to render its own error.
    """
    try:
        import ctypes

        MB_YESNO = 0x00000004
        MB_ICONERROR = 0x00000010
        MB_SETFOREGROUND = 0x00010000
        MB_TOPMOST = 0x00040000
        ID_YES = 6

        box = ctypes.windll.user32.MessageBoxW
        box.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        box.restype = ctypes.c_int
        answer = box(
            None,
            problem.message,
            problem.title,
            MB_YESNO | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST,
        )
        return answer == ID_YES
    except Exception:
        return False


def verify_or_explain() -> bool:
    """True if the UI can render. Otherwise explain the problem and return False.

    Callers should exit quietly on False — the user has already been told what's
    wrong in plain language, and a traceback stacked on top of that helps nobody.
    """
    if sys.platform != "win32":
        return True
    if os.environ.get(SKIP_ENV_VAR):
        return True

    try:
        problem = find_problem()
    except Exception:
        # A guard that blocks startup on its own bug is worse than no guard.
        return True
    if problem is None:
        return True

    try:
        print("[STARTUP] " + problem.title + "\n" + problem.message)
    except Exception:
        pass

    if _show_dialog(problem):
        import webbrowser

        try:
            webbrowser.open(problem.url)
        except Exception:
            pass
    return False
