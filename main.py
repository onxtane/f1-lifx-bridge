import json
import os
import sys
import threading
from pathlib import Path

# Console output must not depend on the OS codepage. A cp1252 stdout raises
# UnicodeEncodeError on any character it can't map (an arrow in a setup hint was
# enough to kill the bridge thread — #76), so force UTF-8 and never fail on an
# unmappable character. Silently skipped when the stream can't be reconfigured
# (e.g. no stdout in a windowed build).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# On Windows, use the WebView2 (edgechromium) backend — it renders via the OS's
# auto-updating WebView2 runtime, so the build ships no bundled Chromium (~15 MB
# vs ~210 MB on Qt). WebView2 is Chromium under the hood, so the same flags keep
# its compositor running while a fullscreen game occludes the window (preventing
# the blank-frame flicker on resume) — passed via WebView2's argument env var.
# macOS uses pywebview's native Cocoa/WKWebView backend; Linux keeps its default.
if sys.platform == "win32":
    os.environ.setdefault("PYWEBVIEW_GUI", "edgechromium")
    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--disable-backgrounding-occluded-windows --disable-renderer-backgrounding",
    )

import webview

from bridge_runner import BridgeRunner

import runtime_check
from app_paths import BUNDLE_DIR, USER_DATA_DIR

UI_FILE = BUNDLE_DIR / "ui" / "index.html"


class Api:
    """
    Exposed to the page as `pywebview.api.*`.

    All Python→JS updates are queued here and pulled by the JS side
    via get_pending_updates() on a 250 ms interval.  This means Python
    never calls evaluate_js, eliminating Qt compositor repaints that
    caused UI flickering.
    """

    def __init__(self):
        self._window: webview.Window | None = None
        self._queue: list = []
        self._queue_lock = threading.Lock()

        self.runner = BridgeRunner(
            on_log=self._push_log,
            on_state_change=self._push_state,
            on_status_text=self._push_status_text,
            on_stat=self._push_stat,
            on_lights_discovered=self._push_lights,
            on_discovering=self._push_discovering,
            on_selection_changed=self._push_selection,
        )

    def set_window(self, window: webview.Window):
        self._window = window

    # ---- JS polling endpoint ----

    def get_pending_updates(self):
        """Called by JS every 250 ms. Returns queued [[fn, args], ...] and clears the queue."""
        with self._queue_lock:
            if not self._queue:
                return None
            batch = self._queue[:]
            self._queue.clear()
        return batch

    # ---- bridge control ----

    def start_bridge(self):
        self.runner.start()
        return {"ok": True}

    def stop_bridge(self):
        self.runner.stop()
        return {"ok": True}

    def discover_lights(self):
        self.runner.discover_lights()
        return {"ok": True}

    def test_selected(self):
        self.runner.test_selected()
        return {"ok": True}

    def test_multizone(self):
        self.runner.test_multizone()
        return {"ok": True}

    def trigger_effect(self, name: str):
        self.runner.trigger_effect(name)
        return {"ok": True}

    # ---- lights / group management ----

    def get_discovered_lights(self):
        return self.runner.get_discovered_lights()

    def set_selected_lights(self, labels: list):
        self.runner.set_selected_lights(labels)
        return {"ok": True}

    def get_groups(self):
        return self.runner.get_groups()

    def save_group(self, name: str, labels: list):
        self.runner.save_group(name, labels)
        return {"ok": True}

    def delete_group(self, name: str):
        self.runner.delete_group(name)
        return {"ok": True}

    def load_group(self, name: str):
        missing = self.runner.load_group(name)
        if missing:
            labels = ", ".join(sorted(missing))
            n = len(missing)
            self._push_toast(
                f"{n} device{'s' if n != 1 else ''} not found: {labels}",
                "warning",
                4000,
            )
        return {"ok": True}

    # ---- GUI settings ----

    def get_gui_settings(self):
        return self.runner.get_gui_settings()

    def save_gui_settings(self, data: dict):
        self.runner.save_gui_settings(data)
        return {"ok": True}

    def set_enabled_events(self, names):
        self.runner.set_enabled_events(names)
        return {"ok": True}

    def set_brightness_range(self, min_pct: int, max_pct: int):
        self.runner.set_brightness_range(min_pct, max_pct)
        return {"ok": True}

    def set_stagger(self, enabled: bool, ms: int):
        self.runner.set_stagger(enabled, ms)
        return {"ok": True}

    def set_idle_state(self, color_hex: str, pulse: bool):
        self.runner.set_idle_state(color_hex, pulse)
        return {"ok": True}

    def set_forwarding(self, enabled: bool, host: str, port: int):
        self.runner.set_forwarding(enabled, host, port)
        return {"ok": True}

    def set_mz_startlights(self, direction: str, mode: str):
        self.runner.set_mz_startlights(direction, mode)
        return {"ok": True}

    def set_debug_timing(self, enabled: bool):
        self.runner.set_debug_timing(enabled)
        return {"ok": True}

    def set_nanoleaf_diag(self, enabled: bool):
        self.runner.set_nanoleaf_diag(enabled)
        return {"ok": True}

    # ---- Nanoleaf ----

    def get_nanoleaf_settings(self):
        return self.runner.get_nanoleaf_settings()

    def get_nanoleaf_device_info(self):
        return self.runner.get_nanoleaf_device_info()

    def save_nanoleaf_settings(self, data: dict):
        self.runner.save_nanoleaf_settings_data(data)
        return {"ok": True}

    def pair_nanoleaf(self, ip: str):
        return self.runner.pair_nanoleaf(ip)

    def discover_nanoleaf(self):
        return self.runner.discover_nanoleaf_devices()

    def set_nanoleaf_enabled(self, enabled: bool):
        self.runner.set_nanoleaf_enabled(enabled)
        return {"ok": True}

    # ---- Hue ----

    def get_hue_settings(self):
        return self.runner.get_hue_settings()

    def save_hue_settings(self, data: dict):
        self.runner.save_hue_settings_data(data)
        return {"ok": True}

    def set_hue_enabled(self, enabled: bool):
        self.runner.set_hue_enabled(enabled)
        return {"ok": True}

    def pair_hue(self, ip: str):
        return self.runner.pair_hue(ip)

    def discover_hue(self):
        return self.runner.discover_hue_devices()

    def get_hue_lights(self):
        return self.runner.get_hue_lights()

    def set_hue_diag(self, enabled: bool):
        self.runner.set_hue_diag(enabled)
        return {"ok": True}

    def get_nanoleaf_layout(self):
        return self.runner.get_nanoleaf_layout()

    def save_nanoleaf_layout(self, panels: list):
        self.runner.save_nanoleaf_layout(panels)
        return {"ok": True}

    def reset_nanoleaf_layout(self):
        return self.runner.reset_nanoleaf_layout()

    def set_mini_mode(self, mini: bool):
        if self._window is None:
            return {"ok": False}
        if mini:
            self._window.resize(380, 100)
        else:
            self._window.resize(1320, 860)
        return {"ok": True}

    def copy_to_clipboard(self, text: str):
        if sys.platform == "win32":
            import ctypes
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002
            encoded = (text + "\0").encode("utf-16-le")
            kernel32 = ctypes.windll.kernel32
            user32   = ctypes.windll.user32
            kernel32.GlobalAlloc.restype       = ctypes.c_void_p
            kernel32.GlobalAlloc.argtypes      = [ctypes.c_uint, ctypes.c_size_t]
            kernel32.GlobalLock.restype        = ctypes.c_void_p
            kernel32.GlobalLock.argtypes       = [ctypes.c_void_p]
            kernel32.GlobalUnlock.argtypes     = [ctypes.c_void_p]
            kernel32.GlobalFree.argtypes       = [ctypes.c_void_p]
            user32.SetClipboardData.argtypes   = [ctypes.c_uint, ctypes.c_void_p]
            hMem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
            if not hMem:
                return {"ok": False}
            ptr = kernel32.GlobalLock(hMem)
            if not ptr:
                kernel32.GlobalFree(hMem)
                return {"ok": False}
            ctypes.memmove(ptr, encoded, len(encoded))
            kernel32.GlobalUnlock(hMem)
            user32.OpenClipboard(0)
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_UNICODETEXT, hMem)
            user32.CloseClipboard()
        return {"ok": True}

    def get_lan_interfaces(self):
        return self.runner.get_lan_interfaces()

    def set_listen_address(self, ip: str, port: int):
        self.runner.set_listen_address(ip, int(port))
        return {"ok": True}

    def set_game_mode(self, mode: str):
        self.runner.set_game_mode(mode)
        return {"ok": True}

    def install_wrc_config(self, port=None):
        return self.runner.install_wrc_config(port)

    # ---- developer / self-test (hidden unless dev mode) ----

    def is_dev_mode(self) -> bool:
        """Dev mode is on when running from source, or when GRIDGLOW_DEV=1.

        Gates the in-app 'Run self-tests' button so end users of the frozen .exe
        never see it (and the tests/ folder is never bundled into the build).
        """
        return (not getattr(sys, "frozen", False)
                or os.environ.get("GRIDGLOW_DEV") == "1")

    def run_self_tests(self) -> dict:
        """Run the tests/ unittest suite in-process and return a result summary."""
        import io
        import contextlib
        import unittest

        tests_dir = BUNDLE_DIR / "tests"
        if not tests_dir.is_dir():
            return {"ok": False, "total": 0, "failures": 0, "errors": 0,
                    "output": "tests/ directory not found — this is a dev-only "
                              "feature and tests are not bundled into the build."}

        buf = io.StringIO()
        try:
            suite = unittest.TestLoader().discover(
                str(tests_dir), top_level_dir=str(BUNDLE_DIR))
            runner = unittest.TextTestRunner(stream=buf, verbosity=2)
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                result = runner.run(suite)
            return {
                "ok": result.wasSuccessful(),
                "total": result.testsRun,
                "failures": len(result.failures),
                "errors": len(result.errors),
                "output": buf.getvalue(),
            }
        except Exception as exc:
            return {"ok": False, "total": 0, "failures": 0, "errors": 0,
                    "output": f"{buf.getvalue()}\n\nRunner error: {exc}"}

    def set_light_assignments(self, data: dict):
        self.runner.set_light_assignments(data)
        return {"ok": True}

    def identify_light(self, label: str):
        self.runner.identify_light(label)
        return {"ok": True}

    # ---- internal: enqueue updates for JS to poll ----

    def _enqueue(self, fn_name: str, *args):
        with self._queue_lock:
            self._queue.append([fn_name, list(args)])

    def _push_log(self, line: str):
        self._enqueue("appendLog", line)

    def _push_toast(self, message: str, type: str = "success", duration_ms: int = 2200):
        self._enqueue("showToast", message, type, duration_ms)

    def _push_stat(self, key: str, value: str):
        self._enqueue("setStat", key, value)

    def _push_state(self, running: bool):
        self._enqueue("setBridgeRunning", running)

    def _push_status_text(self, text: str):
        self._enqueue("setStatusText", text)

    def _push_lights(self, lights: list):
        self._enqueue("setLights", lights)

    def _push_discovering(self, active: bool):
        self._enqueue("setDiscovering", active)

    def _push_selection(self, labels: list):
        self._enqueue("setActiveLabels", labels)


def main():
    # Before pywebview touches the rendering stack: if WebView2 or .NET is
    # missing it neither renders nor fails usefully, so say what's wrong while we
    # still can (#72).
    if not runtime_check.verify_or_explain():
        return

    api = Api()

    window = webview.create_window(
        "GridGlow",
        url=UI_FILE.as_uri(),
        js_api=api,
        width=1320,
        height=860,
        min_size=(320, 80),
        background_color="#0b1020",
    )
    api.set_window(window)

    storage = USER_DATA_DIR / "webview_storage"
    webview.start(private_mode=False, storage_path=str(storage))


if __name__ == "__main__":
    main()
