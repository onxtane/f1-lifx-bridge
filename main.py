import json
import os
import sys
import threading
from pathlib import Path

# Force the Qt (PySide6) backend to avoid WebView2 threading issues on Windows.
os.environ.setdefault("PYWEBVIEW_GUI", "qt")

import webview

from bridge_runner import BridgeRunner

if getattr(sys, 'frozen', False):
    # Running as a PyInstaller bundle.
    # Bundled data files (ui/, etc.) are in sys._MEIPASS.
    # User-writable files (settings, storage) go next to the EXE.
    _BUNDLE_DIR = Path(sys._MEIPASS)
    _APP_DIR    = Path(sys.executable).parent
else:
    _BUNDLE_DIR = Path(__file__).resolve().parent
    _APP_DIR    = _BUNDLE_DIR

UI_FILE = _BUNDLE_DIR / "ui" / "index.html"


class Api:
    """
    Exposed to the page as `pywebview.api.*`.

    All Python→JS updates are queued here and pulled by the JS side
    via get_pending_updates() on a 250 ms interval.  This means Python
    never calls evaluate_js, eliminating Qt compositor repaints that
    caused UI flickering.
    """

    def __init__(self):
        self.window: webview.Window | None = None
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
        self.window = window

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

    def get_nanoleaf_layout(self):
        return self.runner.get_nanoleaf_layout()

    def save_nanoleaf_layout(self, panels: list):
        self.runner.save_nanoleaf_layout(panels)
        return {"ok": True}

    def reset_nanoleaf_layout(self):
        return self.runner.reset_nanoleaf_layout()

    def set_mini_mode(self, mini: bool):
        if self.window is None:
            return {"ok": False}
        if mini:
            self.window.resize(380, 100)
        else:
            self.window.resize(1320, 860)
        return {"ok": True}

    def get_lan_interfaces(self):
        return self.runner.get_lan_interfaces()

    def set_listen_address(self, ip: str, port: int):
        self.runner.set_listen_address(ip, int(port))
        return {"ok": True}

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
    api = Api()

    window = webview.create_window(
        "F1 LIFX Bridge",
        url=UI_FILE.as_uri(),
        js_api=api,
        width=1320,
        height=860,
        min_size=(320, 80),
        background_color="#0b1020",
    )
    api.set_window(window)

    storage = _APP_DIR / "webview_storage"
    webview.start(private_mode=False, storage_path=str(storage))


if __name__ == "__main__":
    main()
